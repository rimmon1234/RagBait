import Link from "next/link";
import { BookOpen, CheckCircle, Lock, Users } from "lucide-react";

export default function HomePage() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
      <div className="text-center">
        <span className="rounded-full bg-blue-100 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-blue-700">
          Production Foundation — Milestones M0 & M1
        </span>
        <h1 className="mt-4 text-4xl font-extrabold tracking-tight text-gray-900 sm:text-5xl">
          Welcome to <span className="text-blue-600">RagBait</span>
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-lg text-gray-600">
          A secure, collaborative, multi-user RAG platform for college study groups and classrooms.
        </p>

        <div className="mt-8 flex justify-center gap-4">
          <Link
            href="/register"
            className="rounded-lg bg-blue-600 px-6 py-3 font-semibold text-white shadow hover:bg-blue-700"
          >
            Create an Account
          </Link>
          <Link
            href="/login"
            className="rounded-lg border border-gray-300 bg-white px-6 py-3 font-semibold text-gray-700 shadow-sm hover:bg-gray-50"
          >
            Sign In
          </Link>
        </div>
      </div>

      <div className="mt-16 grid grid-cols-1 gap-8 md:grid-cols-3">
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
            <Lock className="h-5 w-5" />
          </div>
          <h2 className="mt-4 text-lg font-bold text-gray-900">Strict Authorization</h2>
          <p className="mt-2 text-sm text-gray-600">
            Row-Level Security (RLS) with transaction-local claims, column-level permission revokes, and immutable audit logs.
          </p>
        </div>

        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
            <Users className="h-5 w-5" />
          </div>
          <h2 className="mt-4 text-lg font-bold text-gray-900">Access Review Pipeline</h2>
          <p className="mt-2 text-sm text-gray-600">
            Mandatory email OTP verification before access request submission. Super-admin protections preventing demotion of the last owner.
          </p>
        </div>

        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
            <BookOpen className="h-5 w-5" />
          </div>
          <h2 className="mt-4 text-lg font-bold text-gray-900">Future RAG Ready</h2>
          <p className="mt-2 text-sm text-gray-600">
            Engineered to support subject-scoped RAG, collaborative PDF knowledge bases, and multi-provider AI study assistants in M2–M5.
          </p>
        </div>
      </div>
    </div>
  );
}
