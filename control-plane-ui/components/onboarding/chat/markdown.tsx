import type { ReactNode } from "react";

/**
 * Minimal, dependency-free Markdown for assistant replies: headings, bold/italic, inline and
 * fenced code, bullet/numbered lists, tables and http(s) links. It builds React elements only
 * (no HTML injection), so model output can never inject markup.
 */

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\)|\*[^*\s][^*]*\*)/g;

function inline(text: string, keyBase: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const key = `${keyBase}-${i}`;
    if (!part) return null;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={key} className="font-semibold text-ink-900">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={key} className="rounded bg-base-100 px-1 py-0.5 font-mono text-[0.85em]">
          {part.slice(1, -1)}
        </code>
      );
    }
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    if (link) {
      return (
        <a
          key={key}
          href={link[2]}
          target="_blank"
          rel="noreferrer noopener"
          className="text-accent-600 underline"
        >
          {link[1]}
        </a>
      );
    }
    if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    return part;
  });
}

const isTableRow = (l: string) => l.trim().startsWith("|") && l.trim().endsWith("|") && l.trim().length > 1;
const isDivider = (l: string) => /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/.test(l.trim());
const cells = (l: string) =>
  l
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let n = 0;

  while (i < lines.length) {
    const line = lines[i];
    const key = `b${n++}`;

    if (!line.trim()) {
      i++;
      continue;
    }

    if (line.trim().startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) body.push(lines[i++]);
      i++;
      out.push(
        <pre
          key={key}
          className="overflow-x-auto rounded-lg bg-base-100 p-3 font-mono text-xs text-ink-800"
        >
          {body.join("\n")}
        </pre>,
      );
      continue;
    }

    if (isTableRow(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(cells(lines[i++]));
      out.push(
        <div key={key} className="overflow-x-auto rounded-lg border border-base-200">
          <table className="w-full border-collapse text-left text-xs">
            <thead className="bg-base-100 text-ink-600">
              <tr>
                {head.map((h, c) => (
                  <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                    {inline(h, `${key}h${c}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-t border-base-200">
                  {head.map((_, c) => (
                    <td key={c} className="px-3 py-1.5 align-top text-ink-800">
                      {inline(r[c] ?? "", `${key}r${ri}c${c}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      out.push(
        <p key={key} className="pt-1 text-sm font-semibold text-ink-900">
          {inline(heading[2], key)}
        </p>,
      );
      i++;
      continue;
    }

    const bullet = BULLET.test(line);
    if (bullet || NUMBERED.test(line)) {
      const re = bullet ? BULLET : NUMBERED;
      const items: string[] = [];
      while (i < lines.length && re.test(lines[i])) items.push(re.exec(lines[i++])![1]);
      const List = bullet ? "ul" : "ol";
      out.push(
        <List
          key={key}
          className={`space-y-1 pl-5 ${bullet ? "list-disc" : "list-decimal"} marker:text-ink-500`}
        >
          {items.map((t, li) => (
            <li key={li}>{inline(t, `${key}l${li}`)}</li>
          ))}
        </List>,
      );
      continue;
    }

    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith("```") &&
      !BULLET.test(lines[i]) &&
      !NUMBERED.test(lines[i]) &&
      !/^#{1,4}\s/.test(lines[i]) &&
      !isTableRow(lines[i])
    ) {
      para.push(lines[i++]);
    }
    if (para.length === 0) {
      para.push(lines[i++]); // lone line that matched no block rule; never loop forever
    }
    out.push(
      <p key={key} className="whitespace-pre-wrap leading-relaxed">
        {inline(para.join("\n"), key)}
      </p>,
    );
  }

  return <div className="space-y-2.5">{out}</div>;
}
