"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BookOpen, FileText, LayoutDashboard, ShieldCheck, UserCheck, Users } from "lucide-react";

const navItems = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard },
  { href: "/admin/knowledge-base", label: "Knowledge Base", icon: BookOpen },
  { href: "/admin/requests", label: "Access Requests", icon: UserCheck },
  { href: "/admin/users", label: "Users & Roles", icon: Users },
  { href: "/admin/presence", label: "Active Presence", icon: Activity },
  { href: "/admin/audit", label: "Audit Logs", icon: FileText },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <div className="mb-8 flex items-center justify-between border-b border-gray-200 pb-5">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-6 w-6 text-blue-600" />
            <h1 className="text-2xl font-bold text-gray-900">Administration Console</h1>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            Platform governance, user authorization, and activity monitoring
          </p>
        </div>
      </div>

      <div className="mb-6 flex flex-wrap gap-2 border-b border-gray-200 pb-2">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive =
            pathname === item.href ||
            (item.href !== "/admin" && pathname.startsWith(item.href));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-blue-600 text-white"
                  : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
              }`}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </div>

      <div>{children}</div>
    </div>
  );
}
