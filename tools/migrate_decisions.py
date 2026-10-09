"""One-time migration of historical review ranges. Dry run unless --write is supplied."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def migrate(records, papers):
    migrated, unresolved = [], []
    for index, record in enumerate(records):
        item = dict(record)
        nested = item.get('source_span') or {}
        start = item.get('start', nested.get('start', item.get('source_start')))
        end = item.get('end', nested.get('end', item.get('source_end')))
        expected = nested.get('exact_text', item.get('source_expression', item.get('matched_expression', '')))
        try:
            path = (papers / item['file']).resolve()
            path.relative_to(papers.resolve())
            source = path.read_text(encoding='utf-8')
            if start is None:
                line, column = int(item['line']), int(item['column'])
                lines = source.splitlines(keepends=True)
                if not 1 <= line <= len(lines) or not 1 <= column <= len(lines[line - 1]):
                    raise ValueError('invalid line/column')
                start = sum(map(len, lines[:line - 1])) + column - 1
                end = start + len(expected)
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source):
                raise ValueError('invalid source range')
            actual = source[start:end]
            digest = hashlib.sha256(actual.encode('utf-8')).hexdigest()
            if item.get('source_hash'):
                if item['source_hash'] != digest:
                    raise ValueError('saved source hash differs')
            elif actual != expected:
                raise ValueError('saved expression differs at its original location')
            item.update(start=start, end=end, source_hash=digest)
        except (OSError, ValueError, KeyError, TypeError) as error:
            item.update(start=None, end=None, migration_error=str(error))
            unresolved.append({'index': index, 'file': item.get('file'), 'reason': str(error)})
        item.setdefault('decision_id', hashlib.sha256(
            json.dumps(record, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest())
        for field in ('source_span', 'source_start', 'source_end', 'source_expression',
                      'candidate_provenance', 'full_text'):
            item.pop(field, None)
        migrated.append(item)
    return migrated, unresolved


def main():
    root = Path(__file__).resolve().parents[1]
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--decisions', type=Path, default=root / 'decisions/review_results.json')
    cli.add_argument('--papers', type=Path, default=root / 'papers')
    cli.add_argument('--write', action='store_true')
    args = cli.parse_args()
    records = json.loads(args.decisions.read_text(encoding='utf-8'))
    migrated, unresolved = migrate(records, args.papers)
    # Both verified and unresolved entries are appended by migrate(). Never
    # filter unresolved decisions out of the active list.
    if len(migrated) != len(records):
        raise ValueError('Migration must retain every decision')
    for before, after in zip(records, migrated):
        if any(before.get(key) != after.get(key) for key in
               ('file', 'semantic_macro', 'decision', 'arguments', 'semantic_suggestion')):
            raise ValueError('Migration must preserve every human decision')
    report = {'total': len(records), 'retained': len(migrated),
              'migrated': len(records) - len(unresolved), 'unresolved': unresolved}
    if args.write:
        backup = args.decisions.with_suffix('.pre-simplification.json')
        if backup.exists():
            raise FileExistsError(f'Refusing to overwrite backup: {backup}')
        shutil.copy2(args.decisions, backup)
        args.decisions.write_text(json.dumps(migrated, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        args.decisions.with_suffix('.migration-report.json').write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({**report, 'unresolved': len(unresolved)}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
