export function NotFound({ path }: { path: string }) {
  return (
    <>
      <h1>Page not found</h1>
      <p>There is no page at <code>{path}</code>.</p>
      <p><a href="#/">Back to the status page</a></p>
    </>
  );
}
