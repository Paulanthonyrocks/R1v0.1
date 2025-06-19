import React from "react";
import Link from "next/link";

export default function UnauthorizedPage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-red-50">
      <h1 className="text-4xl font-bold text-red-600 mb-4">Unauthorized</h1>
      <p className="text-lg text-gray-700 mb-6">You do not have permission to access this page.</p>
      <Link href="/login" className="text-blue-600 hover:underline">Return to Login</Link>
    </div>
  );
}