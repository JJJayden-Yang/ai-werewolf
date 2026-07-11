type PendingBackendPanelProps = {
  title: string;
  apiName: string;
  children?: React.ReactNode;
};

export function PendingBackendPanel({
  apiName,
  children,
  title
}: PendingBackendPanelProps) {
  return (
    <section className="pending-panel">
      <p className="eyebrow">Pending Backend</p>
      <h1 className="page-title">{title}</h1>
      <div className="pending-api">{apiName}</div>
      {children ? <div className="pending-body">{children}</div> : null}
    </section>
  );
}
