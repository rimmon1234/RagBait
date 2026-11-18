import Link from "next/link";
import { XCircle } from "lucide-react";

export default function RejectedAccessPage() {
  return (
    <div className="flex min-h-[80vh] items-center justify-center px-4 py-12">
      <div className="w-full max-w-lg rounded-xl border border-gray-200 bg-white p-8 text-center shadow-sm">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-red-100 text-red-600">
          <XCircle className="h-8 w-8" />
        </div>

        <h1 className="mt-4 text-2xl font-bold text-gray-900">Access Request Not Approved</h1>
        <p className="mt-2 text-sm text-gray-600">
          An administrator reviewed your application and was unable to approve platform access at this time.
        </p>

        <div className="mt-6 rounded-lg bg-gray-50 p-4 text-left text-sm text-gray-700">
          <h2 className="font-semibold text-gray-900">Possible Reasons:</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-gray-600">
            <li>Unrecognized college email domain or student ID.</li>
            <li>Class or study cohort capacity reached.</li>
            <li>Incomplete registration details.</li>
          </ul>
        </div>

        <div className="mt-8 flex justify-center gap-4">
          <Link
            href="/"
            className="rounded-lg border border-gray-300 bg-white px-5 py-2.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
          >
            Back to Home
          </Link>
        </div>
      </div>
    </div>
  );
}
