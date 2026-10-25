import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/Layout";
import { PageLoader } from "./components/ui";
import "./index.css";
import { AuthProvider, PendingUploadProvider, ThemeProvider, ToastProvider, useAuth } from "./lib/app-state";
import Analysis from "./pages/Analysis";
import { CollectionDetail, CollectionsList } from "./pages/Collections";
import HistoryPage from "./pages/History";
import Landing from "./pages/Landing";
import Library from "./pages/Library";
import NewAnalysis from "./pages/NewAnalysis";
import ReportPage from "./pages/Report";
import Settings from "./pages/Settings";
import Studio from "./pages/Studio";

function NotFound() {
  return (
    <div className="py-24 text-center">
      <p className="text-5xl font-bold text-slate-300 dark:text-slate-700">404</p>
      <p className="mt-3 font-semibold">This page does not exist.</p>
      <a href="/" className="mt-4 inline-block text-sm font-semibold text-brand-600 hover:underline">
        Go home
      </a>
    </div>
  );
}

function AppRoutes() {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <PageLoader />;
  if (!me) {
    return location.pathname === "/" ? <Landing /> : <Navigate to="/" replace />;
  }
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<NewAnalysis />} />
        <Route path="/analysis/:jobId" element={<Analysis />} />
        <Route path="/documents/:docId" element={<ReportPage />} />
        <Route path="/studio" element={<Studio />} />
        <Route path="/studio/:docId" element={<Studio />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/collections" element={<CollectionsList />} />
        <Route path="/collections/:id" element={<CollectionDetail />} />
        <Route path="/library" element={<Library />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </AppShell>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <AuthProvider>
          <PendingUploadProvider>
            <BrowserRouter>
              <AppRoutes />
            </BrowserRouter>
          </PendingUploadProvider>
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>,
);
