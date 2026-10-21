"""Evidence payload and bounded, source-preserving context selection."""
import json

from .retrieval import tokenize


def model_evidence(evidence):
    # This exact projection is both budgeted and sent to the generation model.
    payload = []
    for e in evidence:
        item = {'id': e['id'], 'text': e['text'], 'headings': e.get('headings', [])}
        item['source_type'] = e.get('source_type', 'document')
        if e.get('source_type') == 'web':
            item.update(title=e.get('title'), url=e.get('url'), fetched_at=e.get('fetched_at'),
                        published_at=e.get('published_at'), read_status=e.get('read_status'))
        else:
            item.update(document_name=e.get('document_name'), version=e.get('version'))
        if 'table_column' in e:
            item['table_column'] = e['table_column']
        payload.append(item)
    return payload


def context_cost(evidence):
    return len(json.dumps(model_evidence(evidence), ensure_ascii=False).encode('utf-8'))


def _excerpt_window(text, question):
    if question in text:
        anchor = text.index(question)
    else:
        terms = [t for t in set(tokenize(question)) if t in text]
        if not terms:
            return 0, min(24, len(text))
        term = min(terms, key=lambda t: (text.count(t), -len(t), t))
        anchor = text.index(term)
    match_length = len(question) if question in text else len(term)
    # Prefer a nearby sentence boundary, but bound preceding context so the
    # matching term remains visible even with the minimum legal budget.
    lower = max(0, anchor - 24)
    for i in range(anchor - 1, lower - 1, -1):
        if text[i] in '\n。！？.!?':
            return i + 1, anchor + match_length
    return lower, anchor + match_length


def _minimum_cost(candidate, question):
    """Bytes needed by the smallest legal excerpt of one candidate, metadata included."""
    evidence = dict(candidate)
    text = evidence['text']
    start, required_end = _excerpt_window(text, question)
    minimum = max(min(24, len(text) - start), required_end - start)
    evidence['text'] = text[start:start + minimum]
    return context_cost([evidence])


def _source_reservations(candidates, question, top_k):
    """Reserve the first few ranked candidates of each source when sources are mixed.

    Metadata size differs a lot between sources (web pages carry title/URL/time),
    so equal byte shares alone could silently exclude a whole source. Returns
    {position: minimum_cost} for reserved candidates.
    """
    sources = {}
    for position, candidate in enumerate(candidates):
        sources.setdefault(candidate.get('source_type', 'document'), []).append(position)
    if top_k is None or len(sources) < 2:
        return {}
    quota = max(1, top_k // (2 * len(sources)))
    return {p: _minimum_cost(candidates[p], question)
            for positions in sources.values() for p in positions[:quota]}


def select_context(candidates, question, budget, top_k=None, balanced=False):
    selected, decisions = [], []
    reserved = _source_reservations(candidates, question, top_k) if balanced else {}
    for position, candidate in enumerate(candidates):
        evidence = dict(candidate)
        if top_k is not None and len(selected) >= top_k:
            decisions.append({'id': evidence['id'], 'selection': 'final_top_k', 'reason': 'final_evidence_limit'})
            continue
        pending = [p for p in reserved if p > position]
        if position not in reserved and top_k is not None and len(selected) + len(pending) >= top_k:
            decisions.append({'id': evidence['id'], 'selection': 'final_top_k', 'reason': 'source_reservation'})
            continue
        used = context_cost(selected)
        slots = min(len(candidates)-position, (top_k-len(selected)) if top_k is not None else len(candidates))
        # Online pages can be much longer than local chunks. Reserve a share
        # for remaining candidates; unused space rolls forward. Metadata and
        # text are measured together, with the unchanged total byte guard.
        # Bytes still owed to later reserved candidates are kept aside so a
        # mixed query keeps usable evidence from every source.
        available = max(0, budget - used - sum(reserved[p] for p in pending))
        if balanced:
            share = available // max(1, slots)
            if position in reserved:
                share = min(max(share, reserved[position]), available)
            limit = used + share
        else:
            limit = budget
        allocation = {'allocation_bytes':max(0,limit-used)} if balanced else {}
        if context_cost(selected + [evidence]) <= limit:
            selected.append(evidence)
            decisions.append({'id': evidence['id'], 'selection': 'selected', 'reason': 'full_evidence_fits', **allocation})
            continue
        text = evidence['text']
        start, required_end = _excerpt_window(text, question)
        minimum = max(min(24, len(text) - start), required_end - start)
        lo, hi = 0, len(text) - start
        while lo < hi:
            mid = (lo + hi + 1) // 2
            evidence['text'] = text[start:start + mid]
            if context_cost(selected + [evidence]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        if lo >= minimum and lo and text[start:start + lo].strip():
            end = start + lo
            # Use a complete trailing sentence when it preserves enough of the window.
            boundaries = [i + 1 for i in range(start, end) if text[i] in '\n。！？.!?']
            if boundaries and boundaries[-1] - start >= max(minimum, lo // 2):
                end = boundaries[-1]
            evidence['text'] = text[start:end]
            evidence['excerpt'] = {'start': start, 'end': end, 'unit': 'chunk_text_characters'}
            selected.append(evidence)
            decisions.append({'id': evidence['id'], 'selection': 'excerpt', 'reason': 'full_evidence_exceeds_bytes', 'excerpt': evidence['excerpt'], **allocation})
        else:
            decisions.append({'id': evidence['id'], 'selection': 'context_budget', 'reason': 'minimum_excerpt_exceeds_bytes', **allocation})
    state = 'selected' if selected else ('budget_exceeded' if candidates else 'no_candidates')
    return selected, {'status': state, 'unit': 'UTF8_bytes', 'budget': budget, 'used': context_cost(selected),
                      'candidate_count': len(candidates), 'decisions': decisions,
                      'allocation': ('source_reserved_equal_shares_v2' if reserved else 'remaining_candidates_equal_shares_v1') if balanced else 'rank_order_greedy_v1',
                      'method': 'model_evidence_JSON_UTF8_bytes_v2'}
