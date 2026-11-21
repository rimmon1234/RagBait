"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FileText,
  FolderTree,
  History,
  LayoutDashboard,
  ListTodo,
} from "lucide-react";

const kbNavItems = [
  { href: "/admin/knowledge-base", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/admin/knowledge-base/subjects", label: "Subjects & Topics", icon: FolderTree },
  { href: "/admin/knowledge-base/documents", label: "Documents", icon: FileText },
  { href: "/admin/knowledge-base/jobs", label: "Extraction Jobs", icon: ListTodo },
  { href: "/admin/knowledge-base/audit", label: "Audit Activity", icon: History },
];

export function AdminKnowledgeBaseNavigation() {
  const pathname = usePathname();

  return (
    <nav className="mb-6 flex space-x-1 rounded-xl bg-gray-100 p-1">
      {kbNavItems.map((item) => {
        const Icon = item.icon;
        const isActive = item.exact
          ? pathname === item.href
          : pathname.startsWith(item.href);

        return (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium transition-all sm:text-sm ${
              isActive
                ? "bg-white text-blue-600 shadow-sm"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
