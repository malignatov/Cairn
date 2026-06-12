# search_index

A SQLite FTS5 virtual table indexing every searchable text field
across Cairn's entities. The structural mechanism behind the
`state_search` tool and the `find` skill.

Not a regular table — FTS5 stores text in its own internal format and
exposes a `MATCH` operator for queries. The columns below are what
appear in query results; the actual storage layout is opaque.

## Columns

- `entity_type` *(UNINDEXED)* — which entity table the row came from.
  One of: `project`, `decision`, `commitment`, `stakeholder`,
  `interest`, `capability`, `resource`, `suggestion`,
  `slu_observation`, `source`, `life_unit`,
  `great_life_dimension`, `slu_assessment`.
- `entity_id` *(UNINDEXED)* — the source row's id. Pair it with
  `entity_type` and pass to `state_query` to fetch the full entity.
- `title` — short identifying text. The "headline" you see in
  results. Indexed.
- `body` — all other searchable text from the source row,
  concatenated with spaces. Indexed.
- `created_at` *(UNINDEXED)* — when the underlying entity was
  created. Used by the `since` filter on `state_search`.

## How sync works

Three triggers per source table (insert / update / delete) keep the
index in step automatically. Any write to a source entity propagates
to the index in the same transaction; deletes do too. There's nothing
to refresh manually.

On migration from a pre-v5 database, a one-time backfill populates
the index from existing rows. The backfill is idempotent — re-opening
a v5 database doesn't double-insert.

## Tokenizer

`unicode61 remove_diacritics 2` — handles Unicode case-folding and
strips diacritics so *"Valéncia"* and *"Valencia"* match the same
query. Important for any user working across languages or with
non-ASCII content.

## Query syntax via the tool

`state_search(query, entity_types?, since?, limit?)` is the only
public surface. The query string is treated as a phrase match by
default, so *"Surf Bar Valencia"* searches for the whole phrase
rather than treating each word as a separate token. To use FTS5's
Boolean operators (`AND`, `OR`, `NOT`) or prefix matching (`valenc*`),
include them explicitly in the query — the tool detects these and
passes the query through unwrapped.

Returns results ranked by BM25 (higher score = more relevant), each
with a `snippet` string containing `<mark>matched terms</mark>` around
the match span.

## What it doesn't do

- **No semantic matching.** A search for *"ocean"* won't find
  *"Mediterranean"*. The brief calls this out as a deliberate tradeoff:
  lexical matching is transparent — when a result surfaces the user
  can see exactly which word matched — and Cairn's data uses
  consistent vocabulary because it's the user's own writing about
  their own life.
- **No re-ranking with an LLM.** BM25's pure information-retrieval
  ranking is the only signal.
- **No fuzzy matching beyond what the tokenizer provides.** *"Valencia"*
  doesn't match *"Valancia"*. The user retypes.
- **No history or analytics.** Cairn doesn't log what's searched for.
