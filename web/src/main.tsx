import { lazy, StrictMode, Suspense, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { Footer, Nav } from "./components/Chrome";
import Landing from "./pages/Landing";
import "./styles.css";

// The dashboard is its own chunk: visitors who only read the landing page don't download it.
const Dashboard = lazy(() => import("./pages/Dashboard"));

function Layout() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (!hash) window.scrollTo(0, 0);
  }, [pathname, hash]);
  return (
    <div className="flex min-h-screen flex-col">
      <Nav />
      <main className="flex-1">
        <Suspense fallback={<div className="mx-auto max-w-6xl px-6 py-24 text-fg-faint">Loading…</div>}>
          <Outlet />
        </Suspense>
      </main>
      <Footer />
    </div>
  );
}

function NotFound() {
  return (
    <div className="mx-auto max-w-xl px-6 py-32 text-center">
      <div className="eyebrow mb-3">404</div>
      <h1 className="text-3xl font-semibold">This page dropped off.</h1>
      <p className="mt-3 text-fg-muted">Unlike our users, it isn't coming back.</p>
      <Link to="/" className="mt-6 inline-block text-saffron-400 hover:text-saffron-300">← Back home</Link>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Landing />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
