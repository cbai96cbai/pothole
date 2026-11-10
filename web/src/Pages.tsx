import { Layout } from "./components/Layout";
import { About } from "./pages/About";
import { Board } from "./pages/Board";
import { Drills } from "./pages/Drills";
import { Finding } from "./pages/Finding";
import { Findings } from "./pages/Findings";
import { Incidents } from "./pages/Incidents";
import { NotFound } from "./pages/NotFound";
import { Repairs } from "./pages/Repairs";
import type { Route } from "./routes";
import type { Finding as FindingData, SiteData } from "./types";

/** One route as a full page. `finding` is the file for a finding route: undefined while it
 * loads, null when no such finding exists. */
export function Pages({
  route,
  data,
  finding,
}: {
  route: Route;
  data: SiteData;
  finding?: FindingData | null;
}) {
  return (
    <Layout route={route} meta={data.meta}>
      <Body route={route} data={data} finding={finding} />
    </Layout>
  );
}

function Body({ route, data, finding }: { route: Route; data: SiteData; finding?: FindingData | null }) {
  switch (route.page) {
    case "board":
      return <Board board={data.board} backtest={data.backtest} nullSimulation={data.nullSimulation} facts={data.meta.facts} />;
    case "incidents":
      return <Incidents items={data.incidents.incidents} />;
    case "drills":
      return <Drills items={data.drills.drills} />;
    case "findings":
      return <Findings index={data.findingsIndex} />;
    case "finding":
      if (finding === undefined) return <p className="muted">Loading the finding...</p>;
      if (finding === null) return <NotFound path={`/findings/${route.id}`} />;
      return <Finding finding={finding} />;
    case "repairs":
      return <Repairs repairs={data.repairs} />;
    case "about":
      return <About delay={data.delay} nullSimulation={data.nullSimulation} facts={data.meta.facts} />;
    case "notfound":
      return <NotFound path={route.path} />;
  }
}
