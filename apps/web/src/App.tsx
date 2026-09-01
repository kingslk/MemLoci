import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { DreamsPage } from "./pages/Dreams";
import { EvidencePage } from "./pages/Evidence";
import { GraphPage } from "./pages/Graph";
import { JobsPage } from "./pages/Jobs";
import { MemoriesPage } from "./pages/Memories";
import { OverviewPage } from "./pages/Overview";
import { QueryLogsPage } from "./pages/QueryLogs";
import { RepositoriesPage } from "./pages/Repositories";
import { ReviewPage } from "./pages/Review";
import { SettingsPage } from "./pages/Settings";
import { WorkspaceProvider } from "./workspace";

export default function App() {
  return (
    <WorkspaceProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/memories" element={<MemoriesPage />} />
            <Route path="/evidence" element={<EvidencePage />} />
            <Route path="/graph" element={<GraphPage />} />
            <Route path="/query-logs" element={<QueryLogsPage />} />
            <Route path="/dreams" element={<DreamsPage />} />
            <Route path="/repositories" element={<RepositoriesPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </WorkspaceProvider>
  );
}
