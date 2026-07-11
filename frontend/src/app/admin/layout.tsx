import Link from "next/link";

export default function AdminLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="admin-shell">
      <header className="admin-topbar">
        <strong>Admin</strong>
        <Link href="/admin/runs">Audit</Link>
        <Link href="/admin/analysis">Analysis</Link>
        <Link href="/admin/strategy/reviews">Strategy</Link>
        <Link href="/admin/data">Data</Link>
      </header>
      {children}
    </div>
  );
}
