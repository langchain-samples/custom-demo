/**
 * A CSV rendered as a spreadsheet rather than as raw text.
 *
 * The monospace `<pre>` was technically honest and practically unreadable: a claims export
 * is fifteen columns wide, so every record wrapped mid-row and the header scrolled out of
 * reach. Columns you cannot line up are not data you can check, which is the whole point of
 * looking at the agent's files.
 *
 * The parser handles RFC-4180 quoting because the data needs it - an address column carries
 * `"418 Cypress Bend Dr, San Antonio, TX 78248"`, and splitting on commas turns one field
 * into three and shifts every column after it.
 */
import { useMemo } from "react";

/** Rows past this stay unparsed. A preview, not a data grid. */
const MAX_ROWS = 500;

/**
 * Split CSV text into rows of fields, honouring quoted fields (which may contain commas,
 * newlines, and `""` as an escaped quote). A hand-rolled scanner rather than a dependency:
 * this is thirty lines and the alternative ships a parser for one preview pane.
 */
export function parseCsv(text: string, delimiter = ","): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        // A doubled quote inside a quoted field is one literal quote.
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
        }
      } else {
        field += c;
      }
      continue;
    }
    if (c === '"') {
      quoted = true;
    } else if (c === delimiter) {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      // Close the row on \n or a lone \r, and swallow the \n of a \r\n pair.
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += c;
    }
  }
  // A file with no trailing newline still has one row left in hand.
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.length > 1 || r[0] !== "");
}

export function CsvSheet({ text, delimiter = "," }: { text: string; delimiter?: string }) {
  const rows = useMemo(() => parseCsv(text, delimiter), [text, delimiter]);
  if (!rows.length) return null;

  const [header, ...body] = rows;
  const shown = body.slice(0, MAX_ROWS);
  // Ragged rows are normal in exported data; pad so the grid stays rectangular rather
  // than letting a short row pull the borders out of line.
  const width = rows.reduce((w, r) => Math.max(w, r.length), 0);

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {/* The scroll lives HERE, on its own box: a wide table must not push the dialog
          sideways, and the header row has to stay put while you scroll down to row 300. */}
      <div className="min-w-0 overflow-auto rounded-md border border-border">
        <table className="w-full border-collapse text-[12px]">
          <thead className="sticky top-0 z-10">
            <tr>
              {Array.from({ length: width }, (_, i) => (
                <th
                  key={i}
                  className="border-b border-r border-border bg-panel-2 px-2.5 py-1.5 text-left font-semibold whitespace-nowrap text-foreground last:border-r-0"
                >
                  {header[i] ?? ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, ri) => (
              <tr key={ri} className={ri % 2 ? "bg-panel-2/40" : undefined}>
                {Array.from({ length: width }, (_, ci) => (
                  <td
                    key={ci}
                    className="border-b border-r border-border px-2.5 py-1 align-top whitespace-nowrap text-muted-foreground last:border-r-0"
                    title={r[ci] ?? ""}
                  >
                    {r[ci] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="m-0 text-[11px] text-muted-foreground">
        {body.length.toLocaleString()} row{body.length === 1 ? "" : "s"} ×{" "}
        {width.toLocaleString()} column{width === 1 ? "" : "s"}
        {body.length > MAX_ROWS ? ` · showing the first ${MAX_ROWS}` : ""}
      </p>
    </div>
  );
}
