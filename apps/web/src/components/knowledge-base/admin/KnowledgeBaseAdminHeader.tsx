"use client";

import Link from "next/link";
import { BookOpen } from "lucide-react";

interface Props {
  title: string;
  description: string;
  actions?: React.ReactNode;
}

export function KnowledgeBaseAdminHeader({ title, description, actions }: Props) {
  return (
    <div className="mb-6 flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
      <div>
        <div className="flex items-center gap-2">
          <BookOpen className="h-6 w-6 text-blue-600" />
          <h1 className="text-xl font-bold text-gray-900">{title}</h1>
        </div>
        <p className="mt-1 text-sm text-gray-500">{description}</p>
      </div>
      {actions && <div className="flex items-center gap-3">{actions}</div>}
    </div>
  );
}
