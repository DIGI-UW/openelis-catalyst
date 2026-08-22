/**
 * How many lines a hand edit added and removed, for the one-glance summary on
 * an "Edited by hand" cell. A proper LCS on lines: queries are short, so the
 * quadratic table costs nothing, and anything cheaper (set difference) counts
 * a moved line as both added and removed.
 */
export const lineDiffSummary = (
  before: string,
  after: string,
): { added: number; removed: number } => {
  const left = before.split("\n");
  const right = after.split("\n");
  const rows = left.length + 1;
  const columns = right.length + 1;
  const common = new Array<number>(rows * columns).fill(0);
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      common[i * columns + j] =
        left[i] === right[j]
          ? common[(i + 1) * columns + j + 1]! + 1
          : Math.max(common[(i + 1) * columns + j]!, common[i * columns + j + 1]!);
    }
  }
  const shared = common[0]!;
  return { added: right.length - shared, removed: left.length - shared };
};
