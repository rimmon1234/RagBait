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
      className="card-elevated group relative flex flex-col justify-between overflow-hidden p-6 hover:-translate-y-0.5 hover:border-indigo-300 hover:shadow-lg hover:shadow-indigo-900/5"
    >
      <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-blue-500 via-indigo-500 to-cyan-500 opacity-0 transition-opacity duration-200 group-hover:opacity-100" />
      <div>
        <div className="flex items-center justify-between">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-blue-50 to-indigo-50 text-indigo-600 ring-1 ring-indigo-100 transition-all duration-200 group-hover:from-blue-600 group-hover:to-indigo-600 group-hover:text-white group-hover:shadow-md group-hover:shadow-indigo-600/20">
            <IconComponent className="h-6 w-6" />
          </div>
          <span className="inline-flex items-center rounded-full bg-slate-100/90 px-2.5 py-1 text-xs font-semibold text-slate-600 ring-1 ring-slate-200/70">
            {subject.topic_count} {subject.topic_count === 1 ? "topic" : "topics"}
          </span>
        </div>

        <h3 className="mt-4 text-lg font-bold text-slate-900 transition-colors group-hover:text-indigo-700">
          {subject.name}
        </h3>

        {subject.description && (
          <p className="mt-2 line-clamp-2 text-sm text-slate-600 leading-relaxed">
            {subject.description}
          </p>
        )}
      </div>

      <div className="mt-6 flex items-center justify-between border-t border-slate-100 pt-4 text-xs text-slate-500">
        <span className="flex items-center gap-1.5 font-medium">
          <FileText className="h-3.5 w-3.5 text-slate-400" />
          {subject.document_count} {subject.document_count === 1 ? "document" : "documents"}
        </span>
        <span className="font-semibold text-indigo-600 transition-transform duration-200 group-hover:translate-x-0.5">
          Explore topics &rarr;
        </span>
      </div>
    </Link>
  );
}
