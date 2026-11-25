"use client";

import React, { Fragment } from "react";

export interface MarkdownMessageRendererProps {
  content: string;
  isProvisional?: boolean;
  onCitationClick?: (citationKey: string) => void;
}

const CITATION_OR_INLINE_TOKEN_REGEX =
  /(`[^`\n]+`|\*\*[^*\n]+?\*\*|\*[^*\n]+?\*|\[[^\]]+\]\([^)\s]+\)|\[(S\d+)\])/;

function isSafeHref(rawUrl: string): boolean {
  const trimmed = rawUrl.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith("/") && !trimmed.startsWith("//")) {
    return true;
  }
  try {
    const parsed = new URL(trimmed);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

function renderInlineContent(
  text: string,
  options: {
    keyPrefix: string;
    isProvisional: boolean;
    onCitationClick?: (citationKey: string) => void;
  }
): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let remaining = text;
  let partIdx = 0;

  while (remaining.length > 0) {
    const match = CITATION_OR_INLINE_TOKEN_REGEX.exec(remaining);
    if (!match || match.index === undefined) {
      nodes.push(<Fragment key={`${options.keyPrefix}-t-${partIdx++}`}>{remaining}</Fragment>);
      break;
    }

    if (match.index > 0) {
      nodes.push(
        <Fragment key={`${options.keyPrefix}-t-${partIdx++}`}>
          {remaining.slice(0, match.index)}
        </Fragment>
      );
    }

    const token = match[0];
    const citationKey = match[2];

    if (citationKey) {
      nodes.push(
        <button
          key={`${options.keyPrefix}-c-${partIdx++}`}
          type="button"
          onClick={() => options.onCitationClick?.(citationKey)}
          disabled={!options.onCitationClick}
          aria-label={`Inspect source ${citationKey}`}
          className={`mx-0.5 inline-flex items-center rounded-md px-1.5 py-0.5 align-baseline text-[11px] font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 ${
            options.isProvisional
              ? "border border-amber-300 bg-amber-50 text-amber-800"
              : "border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
          } disabled:cursor-default`}
        >
          [{citationKey}]
        </button>
      );
    } else if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(
        <code
          key={`${options.keyPrefix}-code-${partIdx++}`}
          className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[0.85em] text-gray-800 border border-gray-200"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith("**") && token.endsWith("**")) {
      nodes.push(
        <strong key={`${options.keyPrefix}-b-${partIdx++}`} className="font-semibold text-gray-900">
          {renderInlineContent(token.slice(2, -2), {
            ...options,
            keyPrefix: `${options.keyPrefix}-b-${partIdx}`,
          })}
        </strong>
      );
    } else if (token.startsWith("*") && token.endsWith("*")) {
      nodes.push(
        <em key={`${options.keyPrefix}-i-${partIdx++}`} className="italic">
          {renderInlineContent(token.slice(1, -1), {
            ...options,
            keyPrefix: `${options.keyPrefix}-i-${partIdx}`,
          })}
        </em>
      );
    } else if (token.startsWith("[")) {
      const linkMatch = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token);
      if (linkMatch && isSafeHref(linkMatch[2])) {
        nodes.push(
          <a
            key={`${options.keyPrefix}-a-${partIdx++}`}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="font-medium text-blue-600 underline underline-offset-2 hover:text-blue-700"
          >
            {linkMatch[1]}
          </a>
        );
      } else {
        // Unsafe protocol (e.g., javascript:, data:) rendered strictly as plain inert text
        nodes.push(<Fragment key={`${options.keyPrefix}-unsafe-${partIdx++}`}>{token}</Fragment>);
      }
    } else {
      nodes.push(<Fragment key={`${options.keyPrefix}-raw-${partIdx++}`}>{token}</Fragment>);
    }

    remaining = remaining.slice(match.index + token.length);
  }

  return nodes;
}

function isTableSeparatorLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|") || !trimmed.includes("-")) return false;
  const cells = trimmed.replace(/^\|/, "").replace(/\|$/, "").split("|");
  return cells.length >= 1 && cells.every((c) => /^\s*:?-{3,}:?\s*$/.test(c));
}

function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/**
 * Pure React Markdown & citation chip renderer with ZERO HTML string injection (`dangerouslySetInnerHTML`).
 * Supports headings, paragraphs, ordered/unordered lists, blockquotes, fenced code blocks,
 * GFM pipe tables, safe HTTP(S)/relative links, and interactive [Sn] citation chips.
 */
export const MarkdownMessageRenderer = React.memo(function MarkdownMessageRenderer({
  content,
  isProvisional = false,
  onCitationClick,
}: MarkdownMessageRendererProps) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Skip empty lines between blocks
    if (!line.trim()) {
      i += 1;
      continue;
    }

    // 1. Fenced code block (``` or ~~~)
    const fenceMatch = /^(`{3,}|~{3,})([a-zA-Z0-9_+-]*)\s*$/.exec(line.trim());
    if (fenceMatch) {
      const fence = fenceMatch[1];
      const lang = fenceMatch[2];
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push(
        <div
          key={`code-${blocks.length}`}
          className="my-2 overflow-hidden rounded-xl border border-gray-800 bg-gray-900 text-gray-100"
        >
          {lang && (
            <div className="border-b border-gray-800 bg-gray-950/70 px-3 py-1 text-[11px] font-mono uppercase tracking-wide text-gray-400">
              {lang}
            </div>
          )}
          <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed">
            <code>{codeLines.join("\n")}</code>
          </pre>
        </div>
      );
      continue;
    }

    // 2. Headings (# through ######)
    const headingMatch = /^(#{1,6})\s+(.+)$/.exec(line);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const text = headingMatch[2];
      const sizeClasses: Record<number, string> = {
        1: "text-base font-bold text-gray-900 mt-3 mb-1.5",
        2: "text-[15px] font-bold text-gray-900 mt-3 mb-1.5",
        3: "text-sm font-semibold text-gray-900 mt-2.5 mb-1",
        4: "text-sm font-semibold text-gray-800 mt-2 mb-1",
        5: "text-xs font-semibold uppercase tracking-wide text-gray-700 mt-2 mb-1",
        6: "text-xs font-medium uppercase tracking-wide text-gray-600 mt-2 mb-1",
      };
      const Tag = (`h${level}` as unknown) as React.ElementType;
      blocks.push(
        <Tag key={`h-${blocks.length}`} className={sizeClasses[level] || sizeClasses[3]}>
          {renderInlineContent(text, {
            keyPrefix: `h-${blocks.length}`,
            isProvisional,
            onCitationClick,
          })}
        </Tag>
      );
      i += 1;
      continue;
    }

    // 3. Pipe Table (header row + separator row)
    if (line.includes("|") && i + 1 < lines.length && isTableSeparatorLine(lines[i + 1])) {
      const headers = splitTableRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push(
        <div
          key={`tbl-${blocks.length}`}
          className="my-2 overflow-x-auto rounded-lg border border-gray-200 bg-white"
        >
          <table className="min-w-full divide-y divide-gray-200 text-left text-xs">
            <thead className="bg-gray-50">
              <tr>
                {headers.map((h, hIdx) => (
                  <th key={hIdx} className="px-3 py-2 font-semibold text-gray-800">
                    {renderInlineContent(h, {
                      keyPrefix: `th-${blocks.length}-${hIdx}`,
                      isProvisional,
                      onCitationClick,
                    })}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((row, rIdx) => (
                <tr key={rIdx} className="hover:bg-gray-50/70">
                  {headers.map((_, cIdx) => (
                    <td key={cIdx} className="px-3 py-2 text-gray-700 align-top">
                      {renderInlineContent(row[cIdx] || "", {
                        keyPrefix: `td-${blocks.length}-${rIdx}-${cIdx}`,
                        isProvisional,
                        onCitationClick,
                      })}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // 4. Blockquote (>)
    if (/^\s*>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      blocks.push(
        <blockquote
          key={`bq-${blocks.length}`}
          className="my-2 border-l-3 border-blue-300 bg-blue-50/40 pl-3 py-1 text-gray-700 italic"
        >
          {renderInlineContent(quoteLines.join(" "), {
            keyPrefix: `bq-${blocks.length}`,
            isProvisional,
            onCitationClick,
          })}
        </blockquote>
      );
      continue;
    }

    // 5. Unordered list (- or *)
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul
          key={`ul-${blocks.length}`}
          className="my-1.5 list-disc space-y-1 pl-5 text-sm leading-relaxed"
        >
          {items.map((item, idx) => (
            <li key={idx}>
              {renderInlineContent(item, {
                keyPrefix: `ul-${blocks.length}-${idx}`,
                isProvisional,
                onCitationClick,
              })}
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // 6. Ordered list (1. )
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ol
          key={`ol-${blocks.length}`}
          className="my-1.5 list-decimal space-y-1 pl-5 text-sm leading-relaxed"
        >
          {items.map((item, idx) => (
            <li key={idx}>
              {renderInlineContent(item, {
                keyPrefix: `ol-${blocks.length}-${idx}`,
                isProvisional,
                onCitationClick,
              })}
            </li>
          ))}
        </ol>
      );
      continue;
    }

    // 7. Paragraph (collect contiguous non-block lines)
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(`{3,}|~{3,})/.test(lines[i].trim()) &&
      !/^#{1,6}\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !(lines[i].includes("|") && i + 1 < lines.length && isTableSeparatorLine(lines[i + 1]))
    ) {
      paraLines.push(lines[i]);
      i += 1;
    }

    if (paraLines.length > 0) {
      const blockIdx = blocks.length;
      blocks.push(
        <p key={`p-${blockIdx}`} className="my-1.5 text-sm leading-relaxed break-words">
          {paraLines.map((pLine, lIdx) => (
            <Fragment key={`pl-${lIdx}`}>
              {lIdx > 0 && <br />}
              {renderInlineContent(pLine, {
                keyPrefix: `p-${blockIdx}-${lIdx}`,
                isProvisional,
                onCitationClick,
              })}
            </Fragment>
          ))}
        </p>
      );
    }
  }

  return <div className="space-y-1 text-gray-900">{blocks}</div>;
});
