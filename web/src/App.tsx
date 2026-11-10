import { useEffect, useState } from "react";

import { loadFinding, site } from "./data";
import { Pages } from "./Pages";
import { match, title } from "./routes";
import type { Finding } from "./types";

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => {
      setHash(window.location.hash);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export function App() {
  const route = match(useHash());
  const pageTitle = title(route);
  const [finding, setFinding] = useState<Finding | null | undefined>(undefined);
  const id = route.page === "finding" ? route.id : null;
  useEffect(() => {
    setFinding(undefined);
    if (id === null) return;
    let live = true;
    loadFinding(id).then((f) => live && setFinding(f));
    return () => {
      live = false;
    };
  }, [id]);
  useEffect(() => {
    document.title = pageTitle;
  }, [pageTitle]);
  return <Pages route={route} data={site} finding={finding} />;
}
