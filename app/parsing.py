"""Bounded parsing worker. No network, OCR, or model inference."""
import csv
import io
import json
import re
import sys
from pathlib import Path

from charset_normalizer import from_bytes
from markdown_it import MarkdownIt


def parse(path: Path, suffix: str):
    from .formats import SUPPORTED, parse_extra
    if suffix not in SUPPORTED:
        raise ValueError('unsupported_format')
    if suffix not in {'.pdf','.md','.txt','.csv'}:
        return parse_extra(path,suffix)
    blocks, warnings = [], []
    headings = []
    heading_stack = []

    def add(text, location, kind='paragraph', cells=None):
        if text.strip():
            blocks.append({'text': text, 'normalized': text.replace('\r\n', '\n'),
                           'location': location, 'headings': list(headings), 'kind': kind})
            if cells is not None:
                blocks[-1]['table_cells'] = cells
        if len(blocks) > 20000:
            raise ValueError('too_many_blocks')

    if suffix == '.pdf':
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > 300:
                raise ValueError('too_many_pages')
            covered = 0
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ''
                if len(text.strip()) < 20:
                    warnings.append(f'第 {i} 物理页疑似扫描/低文本覆盖，无 OCR')
                else:
                    covered += 1
                add(text, {'page': i})
                for ti, table in enumerate(page.extract_tables(), 1):
                    if not table:
                        continue
                    header = ' | '.join(str(v or '') for v in table[0])
                    for ri, row in enumerate(table[1:], 2):
                        add(header + '\n' + ' | '.join(str(v or '') for v in row),
                            {'page': i, 'table': ti, 'row': ri}, 'table',
                            [{'column': f'{ci+1}:{str(h or "")}', 'value': str(v or '')}
                             for ci, (h, v) in enumerate(zip(table[0], row))])
            if not covered:
                raise ValueError('no_reliable_pdf_text_OCR_not_supported')
            coverage = {'pages': len(pdf.pages), 'text_pages': covered}
    else:
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            guess = from_bytes(raw).best()
            if guess is None or guess.chaos > 0.1 or guess.encoding not in ('gb18030', 'gbk', 'big5', 'utf_16', 'utf_16_le', 'utf_16_be'):
                raise ValueError('unknown_text_encoding_use_UTF8')
            text = str(guess)
            warnings.append('编码推断为 ' + guess.encoding + '，请检查原文')
        if '\x00' in text:
            raise ValueError('binary_text_rejected')
        coverage = {'lines': len(text.splitlines())}
        if suffix == '.csv':
            reader = csv.reader(io.StringIO(text, newline=''), strict=True)
            header = next(reader, None)
            if not header:
                raise ValueError('empty_csv')
            for row_number, row in enumerate(reader, 2):
                if row_number > 20000:
                    raise ValueError('too_many_rows')
                if len(row) != len(header):
                    raise ValueError('csv_column_count_mismatch')
                add(json.dumps(dict(zip([f'{i+1}:{h}' for i, h in enumerate(header)], row)), ensure_ascii=False),
                    {'row': row_number, 'line_end': reader.line_num, 'header': header}, 'table',
                    [{'column': f'{ci+1}:{h}', 'value': value} for ci, (h, value) in enumerate(zip(header, row))])
        elif suffix == '.md':
            tokens = MarkdownIt('commonmark').enable('table').parse(text)
            lines = text.splitlines()
            used = set()
            for i, token in enumerate(tokens):
                if token.type == 'heading_open':
                    level = int(token.tag[1:])
                    while heading_stack and heading_stack[-1][0] >= level:
                        heading_stack.pop()
                    heading_stack.append((level, tokens[i + 1].content))
                    headings[:] = [title for _, title in heading_stack]
                if token.map and token.type in ('inline', 'fence', 'code_block'):
                    start, end = token.map
                    if (start, end) not in used:
                        add('\n'.join(lines[start:end]), {'line_start': start + 1, 'line_end': end}, token.type)
                        used.add((start, end))
                if token.type == 'table_open' and token.map:
                    start, end = token.map
                    header = lines[start]
                    rows, cells = [], []
                    for part in tokens[i + 1:]:
                        if part.type == 'table_close':
                            break
                        if part.type == 'inline':
                            cells.append(part.content)
                        elif part.type == 'tr_close':
                            rows.append(cells)
                            cells = []
                    for n, values in zip(range(start + 2, end), rows[1:]):
                        add(header + '\n' + lines[n], {'line_start': n + 1, 'line_end': n + 1, 'header_line': start + 1}, 'table')
                        blocks[-1]['table_cells'] = [
                            {'column': f'{ci+1}:{h}', 'value': value}
                            for ci, (h, value) in enumerate(zip(rows[0], values))]
                        used.add((n, n + 1))
        else:
            pending, start = [], 1
            for n, line in enumerate(text.splitlines() + [''], 1):
                if line.strip():
                    if not pending:
                        start = n
                    pending.append(line)
                elif pending:
                    add('\n'.join(pending), {'line_start': start, 'line_end': n - 1})
                    pending = []
    if not blocks:
        raise ValueError('no_text')
    if sum(len(b['text']) for b in blocks) > 2_000_000:
        raise ValueError('extracted_text_limit')
    return {'blocks': blocks, 'warnings': warnings, 'coverage': coverage}


def chunk(parsed, size=900, strategy='legacy', overlap=0):
    if size < 2:
        raise ValueError('chunk_size_too_small')
    if strategy not in {'legacy', 'boundary'} or not 0 <= overlap < size or (strategy == 'legacy' and overlap):
        raise ValueError('chunk_strategy_contract')
    chunks = []
    for bi, block in enumerate(parsed['blocks']):
        raw = block['text']
        base = {'locations': [block['location']], 'headings': block['headings'], 'block': bi}
        cells = block.get('table_cells')
        if cells is not None and len(raw) > size:
            # Split actual cell values, never infer a header from serialized rows.
            # Repeat only short row-identifying fields, within a bounded prefix.
            for cell in cells:
                identifiers = [f'{c["column"]}={c["value"]}' for c in cells
                               if c is not cell and c['value'] and len(c['value']) <= 40]
                context = ' | '.join(identifiers)
                prefix = (f'row={block["location"].get("row", block["location"].get("line_start", ""))} | '
                          + context[:size // 4] + ' | ' + cell['column'] + ':\n')
                prefix = prefix[:min(size - 1, max(1, size // 2))]
                capacity = size - len(prefix)
                value = cell['value']
                for offset in range(0, max(1, len(value)), capacity):
                    end = min(len(value), offset + capacity)
                    chunks.append({**base, 'text': prefix + value[offset:end], 'offset': offset,
                                   'table_column': cell['column'], 'table_schema': [c['column'] for c in cells],
                                   'cell_span': {'start': offset, 'end': end}, 'text_prefix_length': len(prefix)})
        else:
            # Legacy ParseBlocks without explicit cells are ordinary bounded text;
            # a first line is never assumed to be a table header.
            for offset, end in text_spans(raw, size, strategy, overlap):
                item = {**base, 'text': raw[offset:end], 'offset': offset,
                        'source_span': {'start': offset, 'end': end, 'unit': 'block_characters'}}
                if cells is not None:
                    item['table_schema'] = [c['column'] for c in cells]
                chunks.append(item)
    return chunks


def text_spans(text, size, strategy='boundary', overlap=0):
    start = 0
    boundaries = [m.end() for m in re.finditer(r'\n\s*\n|[。！？.!?](?:\s|$)|[。！？]', text)]
    while start < len(text):
        end = min(start + size, len(text))
        if strategy == 'boundary' and end < len(text):
            nearby = [b for b in boundaries if start + size // 2 <= b <= end]
            if nearby:
                end = nearby[-1]
        yield start, end
        if end == len(text):
            break
        next_start = max(start + 1, end - overlap)
        if overlap and strategy == 'boundary':
            aligned = [b for b in boundaries if next_start <= b < end]
            if aligned:
                next_start = aligned[0]
        start = next_start


if __name__ == '__main__':
    try:
        result = parse(Path(sys.argv[1]), sys.argv[2])
        options = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
        result['chunks'] = chunk(result, **options)
        Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    except Exception as exc:
        # Error type only: parser errors may contain document text.
        safe_codes = {'too_many_blocks', 'too_many_pages', 'no_reliable_pdf_text_OCR_not_supported',
                      'unknown_text_encoding_use_UTF8', 'binary_text_rejected', 'empty_csv',
                      'too_many_rows', 'csv_column_count_mismatch', 'no_text', 'extracted_text_limit'}
        print(str(exc) if str(exc) in safe_codes else type(exc).__name__, file=sys.stderr)
        sys.exit(1)
