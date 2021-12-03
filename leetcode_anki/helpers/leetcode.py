import functools
import json
import logging
import math
import os
import time
from functools import cached_property
from typing import Callable, Dict, List, Tuple, Type

# https://github.com/prius/python-leetcode
import diskcache as diskcache
import leetcode.api.default_api  # type: ignore
import leetcode.api_client  # type: ignore
import leetcode.auth  # type: ignore
import leetcode.configuration  # type: ignore
import leetcode.models.graphql_query  # type: ignore
import leetcode.models.graphql_query_get_question_detail_variables  # type: ignore
import leetcode.models.graphql_query_problemset_question_list_variables  # type: ignore
import leetcode.models.graphql_query_problemset_question_list_variables_filter_input  # type: ignore
import leetcode.models.graphql_question_detail  # type: ignore
import urllib3  # type: ignore
from tqdm import tqdm  # type: ignore

CACHE_SLUG_DIR = "cache/cache_slug"


def _get_leetcode_api_client() -> leetcode.api.default_api.DefaultApi:
    """
    Leetcode API instance constructor.

    This is a singleton, because we don't need to create a separate client
    each time
    """

    configuration = leetcode.configuration.Configuration()

    with open('session.txt') as f:
        session_id = f.read()
    csrf_token = leetcode.auth.get_csrf_cookie(session_id)

    configuration.api_key["x-csrftoken"] = csrf_token
    configuration.api_key["csrftoken"] = csrf_token
    configuration.api_key["LEETCODE_SESSION"] = session_id
    configuration.api_key["Referer"] = "https://leetcode.com"
    configuration.debug = False
    api_instance = leetcode.api.default_api.DefaultApi(
        leetcode.api_client.ApiClient(configuration)
    )

    return api_instance


def retry(times: int, exceptions: Tuple[Type[Exception]], delay: float) -> Callable:
    """
    Retry Decorator
    Retries the wrapped function/method `times` times if the exceptions listed
    in `exceptions` are thrown
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(times - 1):
                try:
                    return func(*args, **kwargs)
                except exceptions:
                    logging.exception(
                        "Exception occured, try %s/%s", attempt + 1, times
                    )
                    time.sleep(delay)

            logging.error("Last try")
            return func(*args, **kwargs)

        return wrapper

    return decorator


class LeetcodeData:
    """
    Retrieves and caches the data for problems, acquired from the leetcode API.

    This data can be later accessed using provided methods with corresponding
    names.
    """

    def __init__(self, start: int, stop: int) -> None:
        """
        Initialize leetcode API and disk cache for API responses
        """
        if start < 0:
            raise ValueError(f"Start must be non-negative: {start}")

        if stop < 0:
            raise ValueError(f"Stop must be non-negative: {start}")

        if start > stop:
            raise ValueError(f"Start (){start}) must be not greater than stop ({stop})")

        self._start = start
        self._stop = stop


    @cached_property
    def _api_instance(self) -> leetcode.api.default_api.DefaultApi:
        return _get_leetcode_api_client()

    @cached_property
    def _cache(
        self,
    ) -> Dict[str, leetcode.models.graphql_question_detail.GraphqlQuestionDetail]:
        """
        Cached method to return dict (problem_slug -> question details)
        """
        problem_cache = diskcache.Cache(CACHE_SLUG_DIR)
        problems = []
        if problem_cache:
            print("Existing cache is found")
            for key in problem_cache.iterkeys():
                problems.append(problem_cache.get(key))
        else:
            print("Creating a new cache")
            problems = self._get_problems_data()
            for problem in problems:
                problem_cache[problem.title_slug] = problem
        return {problem.title_slug: problem for problem in problems}

    @cached_property
    def _id_cache(self) -> Dict[str, str]:
        """
        Cached method to return (problem_id -> problem_slug)
        """
        return {problem.question_frontend_id: problem.title_slug for _, problem in self._cache.items()}

    @retry(times=3, exceptions=(urllib3.exceptions.ProtocolError,), delay=5)
    def _get_problems_count(self) -> int:
        api_instance = self._api_instance

        graphql_request = leetcode.models.graphql_query.GraphqlQuery(
            query="""
            query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
              problemsetQuestionList: questionList(
                categorySlug: $categorySlug
                limit: $limit
                skip: $skip
                filters: $filters
              ) {
                totalNum
              }
            }
            """,
            variables=leetcode.models.graphql_query_problemset_question_list_variables.GraphqlQueryProblemsetQuestionListVariables(
                category_slug="",
                limit=1,
                skip=0,
                filters=leetcode.models.graphql_query_problemset_question_list_variables_filter_input.GraphqlQueryProblemsetQuestionListVariablesFilterInput(
                    tags=[],
                    # difficulty="MEDIUM",
                    # status="NOT_STARTED",
                    # list_id="7p5x763",  # Top Amazon Questions
                    # premium_only=False,
                ),
            ),
            operation_name="problemsetQuestionList",
        )

        time.sleep(2)  # Leetcode has a rate limiter
        data = api_instance.graphql_post(body=graphql_request).data

        return data.problemset_question_list.total_num or 0

    @retry(times=3, exceptions=(urllib3.exceptions.ProtocolError,), delay=5)
    def _get_problems_data_page(
        self, offset: int, page_size: int, page: int
    ) -> List[leetcode.models.graphql_question_detail.GraphqlQuestionDetail]:
        api_instance = self._api_instance
        graphql_request = leetcode.models.graphql_query.GraphqlQuery(
            query="""
            query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
              problemsetQuestionList: questionList(
                categorySlug: $categorySlug
                limit: $limit
                skip: $skip
                filters: $filters
              ) {
                questions: data {
                    questionFrontendId
                    title
                    titleSlug
                    categoryTitle
                    freqBar
                    content
                    isPaidOnly
                    difficulty
                    likes
                    dislikes
                    topicTags {
                      name
                      slug
                    }
                    stats
                    hints
                }
              }
            }
            """,
            variables=leetcode.models.graphql_query_problemset_question_list_variables.GraphqlQueryProblemsetQuestionListVariables(
                category_slug="",
                limit=page_size,
                skip=offset + page * page_size,
                filters=leetcode.models.graphql_query_problemset_question_list_variables_filter_input.GraphqlQueryProblemsetQuestionListVariablesFilterInput(),
            ),
            operation_name="problemsetQuestionList",
        )

        time.sleep(2)  # Leetcode has a rate limiter
        data = api_instance.graphql_post(
            body=graphql_request
        ).data.problemset_question_list.questions

        return data

    def _get_problems_data(
        self,
    ) -> List[leetcode.models.graphql_question_detail.GraphqlQuestionDetail]:
        problem_count = self._get_problems_count()

        if self._start > problem_count:
            raise ValueError(
                "Start ({self._start}) is greater than problems count ({problem_count})"
            )

        start = self._start
        stop = min(self._stop, problem_count)

        page_size = min(3000, stop - start + 1)

        problems: List[
            leetcode.models.graphql_question_detail.GraphqlQuestionDetail
        ] = []

        logging.info(f"Fetching {stop - start + 1} problems {page_size} per page")

        for page in tqdm(
            range(math.ceil((stop - start + 1) / page_size)),
            unit="problem",
            unit_scale=page_size,
        ):
            data = self._get_problems_data_page(start, page_size, page)
            problems.extend(data)

        return problems

    async def all_problems_handles(self) -> List[str]:
        """
        Get all problem handles known.

        Example: ["two-sum", "three-sum"]
        """
        return list(self._cache.keys())

    async def all_problems_ids_to_slug(self) -> Dict[str, str]:
        """
        Get a dict of problem_id:title_slug for all known problems.

        Example: {1: "two-sum", 2: "three-sum"}
        """
        return self._id_cache

    def _get_problem_data(
        self, problem_slug: str
    ) -> leetcode.models.graphql_question_detail.GraphqlQuestionDetail:
        """
        TODO: Legacy method. Needed in the old architecture. Can be replaced
        with direct cache calls later.
        """
        cache = self._cache
        if problem_slug in cache:
            return cache[problem_slug]

    async def _get_description(self, problem_slug: str) -> str:
        """
        Problem description
        """
        data = self._get_problem_data(problem_slug)
        return data.content or "No content"

    async def _stats(self, problem_slug: str) -> Dict[str, str]:
        """
        Various stats about problem. Such as number of accepted solutions, etc.
        """
        data = self._get_problem_data(problem_slug)
        return json.loads(data.stats)

    async def submissions_total(self, problem_slug: str) -> int:
        """
        Total number of submissions of the problem
        """
        return int((await self._stats(problem_slug))["totalSubmissionRaw"])

    async def submissions_accepted(self, problem_slug: str) -> int:
        """
        Number of accepted submissions of the problem
        """
        return int((await self._stats(problem_slug))["totalAcceptedRaw"])

    async def description(self, problem_slug: str) -> str:
        """
        Problem description
        """
        return await self._get_description(problem_slug)

    async def difficulty(self, problem_slug: str) -> str:
        """
        Problem difficulty. Returns colored HTML version, so it can be used
        directly in Anki
        """
        data = self._get_problem_data(problem_slug)
        diff = data.difficulty

        if diff == "Easy":
            return "<font color='green'>Easy</font>"

        if diff == "Medium":
            return "<font color='orange'>Medium</font>"

        if diff == "Hard":
            return "<font color='red'>Hard</font>"

        raise ValueError(f"Incorrect difficulty: {diff}")

    async def paid(self, problem_slug: str) -> str:
        """
        Problem's "available for paid subsribers" status
        """
        data = self._get_problem_data(problem_slug)
        return data.is_paid_only

    async def problem_id(self, problem_slug: str) -> str:
        """
        Numerical id of the problem
        """
        data = self._get_problem_data(problem_slug)
        return data.question_frontend_id

    async def likes(self, problem_slug: str) -> int:
        """
        Number of likes for the problem
        """
        data = self._get_problem_data(problem_slug)
        likes = data.likes

        if not isinstance(likes, int):
            raise ValueError(f"Likes should be int: {likes}")

        return likes

    async def dislikes(self, problem_slug: str) -> int:
        """
        Number of dislikes for the problem
        """
        data = self._get_problem_data(problem_slug)
        dislikes = data.dislikes

        if not isinstance(dislikes, int):
            raise ValueError(f"Dislikes should be int: {dislikes}")

        return dislikes

    async def tags(self, problem_slug: str) -> List[str]:
        """
        List of the tags for this problem (string slugs)
        """
        data = self._get_problem_data(problem_slug)
        return list(map(lambda x: x.slug, data.topic_tags))

    async def freq_bar(self, problem_slug: str) -> float:
        """
        Returns percentage for frequency bar
        """
        data = self._get_problem_data(problem_slug)
        return data.freq_bar or 0

    async def title(self, problem_slug: str) -> float:
        """
        Returns problem title
        """
        data = self._get_problem_data(problem_slug)
        return data.title

    async def category(self, problem_slug: str) -> float:
        """
        Returns problem category title
        """
        data = self._get_problem_data(problem_slug)
        return data.category_title
