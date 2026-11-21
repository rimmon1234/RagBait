"use client";

import Link from "next/link";
import {
  BookOpen,
  Bot,
  Boxes,
  Code2,
  Cpu,
  Database,
  FileText,
  FolderOpen,
  Layers,
  Network,
  Server,
  Terminal,
  Zap,
} from "lucide-react";
import type { SubjectRead } from "@/lib/knowledge-base/knowledge-base-types";

// Dynamic Lucide icon lookup with sensible fallbacks
function getSubjectIcon(iconName: string) {
  const normalized = iconName.toLowerCase().replace(/[-_]/g, "");
  switch (normalized) {
    case "database":
    case "dbms":
    case "sql":
      return Database;
    case "cpu":
    case "os":
    case "operatingsystem":
      return Cpu;
    case "network":
    case "networks":
    case "computernetworks":
      return Network;
    case "chip":
    case "vlsi":
    case "microprocessor":
      return Zap;
    case "bot":
    case "ai":
    case "brain":
    case "artificialintelligence":
      return Bot;
    case "code":
    case "programming":
      return Code2;
    case "terminal":
    case "shell":
      return Terminal;
    case "server":
      return Server;
    case "layers":
      return Layers;
    case "boxes":
      return Boxes;
    default:
      return BookOpen;
  }
}

interface SubjectCardProps {
  subject: SubjectRead;
}

export function SubjectCard({ subject }: SubjectCardProps) {
  const IconComponent = getSubjectIcon(subject.icon_name);

  return (
    <Link
      href={`/knowledge-base/${subject.slug}`}
      className="group relative flex flex-col justify-between overflow-hidden rounded-xl border border-gray-200 bg-white p-6 shadow-sm transition-all duration-200 hover:border-blue-400 hover:shadow-md"
    >
      <div>
        <div className="flex items-center justify-between">
          <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-blue-50 text-blue-600 transition-colors group-hover:bg-blue-600 group-hover:text-white">
            <IconComponent className="h-6 w-6" />
          </div>
          <span className="inline-flex items-center rounded-md bg-gray-50 px-2 py-1 text-xs font-medium text-gray-600 ring-1 ring-gray-500/10 ring-inset">
            {subject.topic_count} {subject.topic_count === 1 ? "topic" : "topics"}
          </span>
        </div>

        <h3 className="mt-4 text-lg font-semibold text-gray-900 group-hover:text-blue-600">
          {subject.name}
        </h3>

        {subject.description && (
          <p className="mt-2 line-clamp-2 text-sm text-gray-600">
            {subject.description}
          </p>
        )}
      </div>

      <div className="mt-6 flex items-center justify-between border-t border-gray-100 pt-4 text-xs text-gray-500">
        <span className="flex items-center gap-1.5">
          <FileText className="h-3.5 w-3.5 text-gray-400" />
          {subject.document_count} {subject.document_count === 1 ? "document" : "documents"}
        </span>
        <span className="font-medium text-blue-600 opacity-0 transition-opacity group-hover:opacity-100">
          Explore topics &rarr;
        </span>
      </div>
    </Link>
  );
}
