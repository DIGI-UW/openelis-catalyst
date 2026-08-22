/**
 * One SQL highlight style, used by both renderers.
 *
 * A committed cell shows `<pre>` and the editor below it shows the same
 * dialect in colour, so the two must agree. They agree here by construction:
 * the editor installs this style through `syntaxHighlighting`, and a static
 * cell runs the same parser and the same style through `highlightTree`. There
 * is no second table of colours to drift from the first.
 *
 * Class names rather than inline styles, so the colours live in CSS as Carbon
 * tokens — `--cds-syntax-*`, of which Carbon emits 88, each theme-aware. That
 * is what makes highlighting work in dark mode without a second definition.
 */
import { HighlightStyle } from "@codemirror/language";
import { PostgreSQL, sql } from "@codemirror/lang-sql";
import { highlightTree, tags } from "@lezer/highlight";
// Imported here rather than by each renderer, so anything that adopts this style
// gets the colours with it and cannot be styled by only one of the two.
import "./sqlHighlight.css";

/** Every span this produces, so the stylesheet and tests can enumerate them. */
export type SqlTokenClass =
  | "sql-keyword"
  | "sql-control"
  | "sql-operator"
  | "sql-string"
  | "sql-number"
  | "sql-comment"
  | "sql-type"
  | "sql-function"
  | "sql-punctuation"
  | "sql-name"
  | "sql-invalid";

export const sqlHighlightStyle = HighlightStyle.define([
  { tag: tags.keyword, class: "sql-keyword" },
  { tag: tags.controlKeyword, class: "sql-control" },
  { tag: tags.operatorKeyword, class: "sql-keyword" },
  { tag: tags.modifier, class: "sql-keyword" },
  { tag: tags.operator, class: "sql-operator" },
  { tag: tags.compareOperator, class: "sql-operator" },
  { tag: tags.logicOperator, class: "sql-operator" },
  { tag: tags.arithmeticOperator, class: "sql-operator" },
  { tag: tags.string, class: "sql-string" },
  { tag: tags.special(tags.string), class: "sql-string" },
  { tag: tags.number, class: "sql-number" },
  { tag: tags.integer, class: "sql-number" },
  { tag: tags.float, class: "sql-number" },
  { tag: tags.bool, class: "sql-number" },
  { tag: tags.null, class: "sql-number" },
  { tag: tags.comment, class: "sql-comment" },
  { tag: tags.lineComment, class: "sql-comment" },
  { tag: tags.blockComment, class: "sql-comment" },
  { tag: tags.typeName, class: "sql-type" },
  { tag: tags.function(tags.variableName), class: "sql-function" },
  { tag: tags.punctuation, class: "sql-punctuation" },
  { tag: tags.separator, class: "sql-punctuation" },
  { tag: tags.paren, class: "sql-punctuation" },
  { tag: tags.bracket, class: "sql-punctuation" },
  { tag: tags.variableName, class: "sql-name" },
  { tag: tags.propertyName, class: "sql-name" },
  { tag: tags.labelName, class: "sql-name" },
  { tag: tags.invalid, class: "sql-invalid" },
]);

/** The dialect the editor edits, so the static parse matches it exactly. */
const sqlLanguage = sql({ dialect: PostgreSQL }).language;

export interface SqlSpan {
  text: string;
  /** Absent for text the grammar gives no tag — whitespace, plain identifiers. */
  className?: string;
}

/**
 * Split SQL into styled spans without mounting an editor.
 *
 * `highlightTree` reports only the ranges it has a style for, so the gaps
 * between them are copied through verbatim. Every character of the input
 * appears in the output exactly once, which a test pins: highlighting must
 * never quietly drop or duplicate SQL.
 */
export const highlightSql = (source: string): SqlSpan[] => {
  if (!source) return [];
  const spans: SqlSpan[] = [];
  let cursor = 0;

  const pushPlain = (upTo: number) => {
    if (upTo > cursor) spans.push({ text: source.slice(cursor, upTo) });
  };

  try {
    const tree = sqlLanguage.parser.parse(source);
    highlightTree(tree, sqlHighlightStyle, (from, to, classes) => {
      pushPlain(from);
      spans.push({ text: source.slice(from, to), className: classes });
      cursor = to;
    });
  } catch {
    // A parse failure must never cost the reader their SQL: fall back to the
    // whole string unstyled rather than rendering nothing.
    return [{ text: source }];
  }

  pushPlain(source.length);
  return spans;
};
