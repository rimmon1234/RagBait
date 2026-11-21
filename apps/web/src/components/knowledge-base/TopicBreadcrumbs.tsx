"use client";

import Link from "next/link";
import { ChevronRight, Home } from "lucide-react";

interface BreadcrumbItem {
  label: string;
  href?: string;
}

interface TopicBreadcrumbsProps {
  items: BreadcrumbItem[];
}

export function TopicBreadcrumbs({ items }: TopicBreadcrumbsProps) {
  return (
    <nav className="flex items-center text-sm text-gray-500" aria-label="Breadcrumb">
      <ol className="flex items-center space-x-2">
        <li>
          <Link
            href="/knowledge-base"
            className="flex items-center gap-1 text-gray-500 transition-colors hover:text-gray-900"
          >
            <Home className="h-4 w-4" />
            <span>Knowledge Base</span>
          </Link>
        </li>
        {items.map((item, idx) => {
          const isLast = idx === items.length - 1;
          return (
            <li key={item.label} className="flex items-center space-x-2">
              <ChevronRight className="h-4 w-4 text-gray-400" />
              {isLast || !item.href ? (
                <span className="font-medium text-gray-900" aria-current={isLast ? "page" : undefined}>
                  {item.label}
                </span>
              ) : (
                <Link
                  href={item.href}
                  className="transition-colors hover:text-gray-900"
                >
                  {item.label}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
