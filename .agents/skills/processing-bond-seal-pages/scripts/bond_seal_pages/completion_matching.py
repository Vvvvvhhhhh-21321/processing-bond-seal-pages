from dataclasses import dataclass

from .titles import (
    SourceTitle,
    TitleMatch,
    match_titles,
    normalize_title,
    title_similarity,
)


MATCH_THRESHOLD = 90


@dataclass(frozen=True)
class CompletionMatchPlan:
    matches: dict
    ambiguities: dict
    low_confidence: dict
    unused_pages: frozenset


def _group_items(items):
    groups = {}
    for item in items:
        groups.setdefault(normalize_title(item.title), []).append(item)
    return groups


def _find_ambiguities(items, returned_titles):
    groups = _group_items(items)
    ambiguous_pages = set()
    ambiguous_items = {}
    for returned in returned_titles:
        scores = {
            title_key: title_similarity(group[0].title, returned.title)
            for title_key, group in groups.items()
        }
        if not scores:
            continue
        best_score = max(scores.values())
        tied_titles = [
            title_key for title_key, score in scores.items() if score == best_score
        ]
        if best_score < MATCH_THRESHOLD or len(tied_titles) < 2:
            continue
        ambiguous_pages.add(returned.page)
        for title_key in tied_titles:
            for item in groups[title_key]:
                current = ambiguous_items.get(item.working_paper_id)
                candidate = (returned.page, best_score)
                if current is None or (best_score, -returned.page) > (
                    current[1],
                    -current[0],
                ):
                    ambiguous_items[item.working_paper_id] = candidate
    return ambiguous_pages, ambiguous_items


def _spread_same_title_pages(items, returned_titles, matches):
    for title_key, group in _group_items(items).items():
        group_ids = {item.working_paper_id for item in group}
        if not any(working_paper_id in matches for working_paper_id in group_ids):
            continue
        pages_used_by_other_titles = {
            match.returned_page
            for working_paper_id, match in matches.items()
            if working_paper_id not in group_ids
        }
        candidates_by_page = {}
        for returned in returned_titles:
            if (
                returned.page not in pages_used_by_other_titles
                and normalize_title(returned.title) == title_key
            ):
                candidates_by_page.setdefault(returned.page, returned)
        candidates = [candidates_by_page[page] for page in sorted(candidates_by_page)]
        if not candidates:
            continue
        for index, item in enumerate(group):
            returned = candidates[index % len(candidates)]
            matches[item.working_paper_id] = TitleMatch(
                item.working_paper_id,
                returned.page,
                item.title,
                returned.title,
                100,
            )


def _best_candidate(title, returned_titles, candidate_pages):
    candidates = [
        (title_similarity(title, returned.title), returned.page)
        for returned in returned_titles
        if returned.page in candidate_pages
    ]
    if not candidates:
        return None
    score, page = max(candidates, key=lambda candidate: (candidate[0], -candidate[1]))
    if score == 0:
        return None
    return page, score


def plan_completion_matches(items, returned_titles):
    items = tuple(items)
    returned_titles = tuple(returned_titles)
    ambiguous_pages, ambiguities = _find_ambiguities(items, returned_titles)
    matchable_titles = tuple(
        returned for returned in returned_titles if returned.page not in ambiguous_pages
    )
    generic_matching = match_titles(
        [SourceTitle(item.working_paper_id, item.title) for item in items],
        matchable_titles,
        threshold=MATCH_THRESHOLD,
    )
    matches = {match.source_id: match for match in generic_matching.matches}
    _spread_same_title_pages(items, matchable_titles, matches)

    titled_pages = {returned.page for returned in returned_titles}
    used_pages = {match.returned_page for match in matches.values()}
    unused_pages = titled_pages - used_pages
    low_confidence_pages = unused_pages - ambiguous_pages
    low_confidence = {
        item.working_paper_id: _best_candidate(
            item.title,
            returned_titles,
            low_confidence_pages,
        )
        for item in items
        if item.working_paper_id not in matches
        and item.working_paper_id not in ambiguities
    }
    return CompletionMatchPlan(
        matches,
        ambiguities,
        {key: value for key, value in low_confidence.items() if value is not None},
        frozenset(unused_pages),
    )
