import type { ReactNode } from "react";

import { NAV, type Route } from "../routes";
import type { Meta } from "../types";
import { LastRun } from "./LastRun";

export function Layout({ route, meta, children }: { route: Route; meta: Meta; children: ReactNode }) {
  const current = route.page === "finding" ? "findings" : route.page;
  return (
    <>
      <header className="top">
        <div className="inner">
          <a className="brand" href="#/">Pothole</a>
          <nav aria-label="Pages">
            {NAV.map((n) => (
              <a key={n.href} href={n.href} aria-current={n.page === current ? "page" : undefined}>
                {n.label}
              </a>
            ))}
          </nav>
        </div>
      </header>
      <main>
        <LastRun meta={meta} />
        {children}
      </main>
    </>
  );
}
