from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


_BRACKETED_TITLE = re.compile(
    r"<<\s*(.*?)\s*>>|《\s*(.*?)\s*》|〈\s*(.*?)\s*〉|<\s*([^<>]*?)\s*>",
    re.DOTALL,
)


def extract_bracket_title(text):
    match = _BRACKETED_TITLE.search(text or "")
    if not match:
        return None
    return next(group for group in match.groups() if group is not None).strip()


def normalize_title(text):
    normalized = unicodedata.normalize("NFKC", text or "")
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
        and character not in "<>"
    )


def _canonical_title(text):
    extracted = extract_bracket_title(text)
    return normalize_title(text if extracted is None else extracted)


def title_similarity(a, b):
    left = _canonical_title(a)
    right = _canonical_title(b)
    if left == right:
        return 100
    if not left or not right:
        return 0
    return round(SequenceMatcher(None, left, right).ratio() * 100)


@dataclass(frozen=True)
class SourceTitle:
    source_id: str
    title: str


@dataclass(frozen=True)
class ReturnedTitle:
    page: int
    title: str


@dataclass(frozen=True)
class TitleMatch:
    source_id: str
    returned_page: int
    source_title: str
    returned_title: str
    score: int


@dataclass(frozen=True)
class MatchResult:
    matches: tuple[TitleMatch, ...]
    unmatched_source_ids: tuple[str, ...]
    unused_pages: tuple[int, ...]


@dataclass
class _FlowEdge:
    to: int
    reverse: int
    capacity: int
    cost: int
    score_cost: int


def _add_flow_edge(graph, source, target, capacity, cost, score_cost=0):
    forward = _FlowEdge(target, len(graph[target]), capacity, cost, score_cost)
    backward = _FlowEdge(source, len(graph[source]), 0, -cost, -score_cost)
    graph[source].append(forward)
    graph[target].append(backward)
    return forward


def _best_assignment(group_titles, returned_by_page, threshold):
    page_numbers = sorted(returned_by_page)
    group_count = len(group_titles)
    page_count = len(page_numbers)
    if not group_count or not page_count:
        return {}

    source_node = 0
    group_start = 1
    page_start = group_start + group_count
    sink_node = page_start + page_count
    graph = [[] for _ in range(sink_node + 1)]

    for group_index in range(group_count):
        _add_flow_edge(graph, source_node, group_start + group_index, 1, 0)
    for page_index in range(page_count):
        _add_flow_edge(graph, page_start + page_index, sink_node, 1, 0)

    tie_base = page_count + 1
    tie_weights = [
        tie_base ** (group_count - group_index - 1)
        for group_index in range(group_count)
    ]
    primary_scale = page_count * sum(tie_weights) + 1
    assignment_edges = {}
    for group_index, group_title in enumerate(group_titles):
        for page_index, page_number in enumerate(page_numbers):
            best_record = None
            best_score = -1
            for record in returned_by_page[page_number]:
                score = title_similarity(group_title, record.title)
                if score > best_score:
                    best_record = record
                    best_score = score
            if best_score < threshold:
                continue
            edge = _add_flow_edge(
                graph,
                group_start + group_index,
                page_start + page_index,
                1,
                -best_score * primary_scale + page_index * tie_weights[group_index],
                -best_score,
            )
            assignment_edges[(group_index, page_index)] = (edge, best_record, best_score)

    while True:
        distance = [float("inf")] * len(graph)
        previous_node = [-1] * len(graph)
        previous_edge = [-1] * len(graph)
        in_queue = [False] * len(graph)
        distance[source_node] = 0
        queue = deque([source_node])
        in_queue[source_node] = True

        while queue:
            node = queue.popleft()
            in_queue[node] = False
            for edge_index, edge in enumerate(graph[node]):
                candidate = distance[node] + edge.cost
                if edge.capacity <= 0 or candidate >= distance[edge.to]:
                    continue
                distance[edge.to] = candidate
                previous_node[edge.to] = node
                previous_edge[edge.to] = edge_index
                if not in_queue[edge.to]:
                    queue.append(edge.to)
                    in_queue[edge.to] = True

        if previous_node[sink_node] == -1:
            break

        node = sink_node
        score_delta = 0
        while node != source_node:
            parent = previous_node[node]
            edge = graph[parent][previous_edge[node]]
            score_delta += edge.score_cost
            node = parent
        if score_delta >= 0:
            break

        node = sink_node
        while node != source_node:
            parent = previous_node[node]
            edge = graph[parent][previous_edge[node]]
            edge.capacity -= 1
            graph[node][edge.reverse].capacity += 1
            node = parent

    assignments = {}
    for (group_index, page_index), (edge, record, score) in assignment_edges.items():
        if edge.capacity == 0:
            assignments[group_index] = (page_numbers[page_index], record, score)
    return assignments


def match_titles(sources, returned, threshold=80):
    sources = list(sources)
    returned = list(returned)
    group_order = []
    groups = {}
    for source in sources:
        key = _canonical_title(source.title)
        if key not in groups:
            groups[key] = []
            group_order.append(key)
        groups[key].append(source)

    returned_by_page = {}
    for record in returned:
        returned_by_page.setdefault(record.page, []).append(record)

    group_matches = {}
    used_pages = set()
    for key in group_order:
        exact_pages = [
            page_number
            for page_number, records in returned_by_page.items()
            if page_number not in used_pages
            and any(_canonical_title(record.title) == key for record in records)
        ]
        if not exact_pages:
            continue
        page_number = min(exact_pages)
        record = next(
            record
            for record in returned_by_page[page_number]
            if _canonical_title(record.title) == key
        )
        group_matches[key] = (page_number, record, 100)
        used_pages.add(page_number)

    remaining_groups = [key for key in group_order if key not in group_matches]
    remaining_pages = {
        page_number: records
        for page_number, records in returned_by_page.items()
        if page_number not in used_pages
    }
    assignments = _best_assignment(remaining_groups, remaining_pages, threshold)
    for group_index, (page_number, record, score) in assignments.items():
        key = remaining_groups[group_index]
        group_matches[key] = (page_number, record, score)
        used_pages.add(page_number)

    matches = []
    unmatched = []
    for source in sources:
        key = _canonical_title(source.title)
        if key not in group_matches:
            unmatched.append(source.source_id)
            continue
        page_number, record, score = group_matches[key]
        matches.append(
            TitleMatch(source.source_id, page_number, source.title, record.title, score)
        )

    unused_pages = tuple(sorted(set(returned_by_page) - used_pages))
    return MatchResult(tuple(matches), tuple(unmatched), unused_pages)