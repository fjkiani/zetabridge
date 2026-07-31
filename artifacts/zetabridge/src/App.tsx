import { Switch, Route, Router, Link, useLocation } from "wouter";
import { useHashLocation } from "wouter/use-hash-location";
import { queryClient } from "./lib/queryClient";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEffect, useState } from "react";
import {
  LayoutDashboard,
  MessageSquare,
  Bot,
  Database,
  GitBranch,
  Network,
  Lightbulb,
  Terminal,
  Plug,
  FlaskConical,
  ChevronLeft,
  ChevronRight,
  Radar,
  GitFork,
  ShieldAlert,
  Gem,
  Sparkles,
  Radio,
  Sun,
  Moon,
  Dna,
  Activity, Layers as LayersIcon,
} from "lucide-react";
import DashboardHome from "@/pages/dashboard-home";
import CoPilot from "@/pages/copilot";
import AgentsPage from "@/pages/agents";
import CatalogExplorer from "@/pages/catalog-explorer";
import Survival from "@/pages/survival";
import Layers from "@/pages/layers";
import Datasets from "@/pages/datasets";
import LineageGraph from "@/pages/lineage-graph";
import GraphExplorer from "@/pages/graph-explorer";
import Insights from "@/pages/insights";
import SignalsHub from "@/pages/signals-hub";
import SignalDetail from "@/pages/signal-detail";
import Bridges from "@/pages/bridges";
import Gaps from "@/pages/gaps";
import Value from "@/pages/value";
import Analyst from "@/pages/analyst";
import Opportunities from "@/pages/opportunities";
import LiveSources from "@/pages/live-sources";
import QueryWorkbench from "@/pages/query-workbench";
import Connectors from "@/pages/connectors";
import Benchmarks from "@/pages/benchmarks";
import EgaBritroc from "@/pages/ega-britroc";
import NotFound from "@/pages/not-found";

function ZetaBridgeLogo({ collapsed }: { collapsed: boolean }) {
  return (
    <div className="flex items-center gap-2.5 px-1">
      <svg
        viewBox="0 0 32 32"
        className="w-7 h-7 shrink-0"
        aria-label="ZetaBridge"
        fill="none"
      >
        <rect x="4" y="14" width="4" height="14" rx="1" fill="hsl(193, 100%, 50%)" />
        <rect x="24" y="14" width="4" height="14" rx="1" fill="hsl(193, 100%, 50%)" />
        <path
          d="M6 14 Q16 2 26 14"
          stroke="hsl(193, 100%, 50%)"
          strokeWidth="2.5"
          fill="none"
          strokeLinecap="round"
        />
        <circle cx="11" cy="10" r="2" fill="hsl(193, 100%, 70%)" opacity="0.8" />
        <circle cx="16" cy="6" r="2" fill="hsl(193, 100%, 70%)" opacity="0.6" />
        <circle cx="21" cy="10" r="2" fill="hsl(193, 100%, 70%)" opacity="0.8" />
      </svg>
      {!collapsed && (
        <span className="text-sm font-semibold tracking-tight text-foreground whitespace-nowrap">
          ZetaBridge
        </span>
      )}
    </div>
  );
}

const navItems = [
  { path: "/", label: "Overview", icon: LayoutDashboard },
  { path: "/opportunities", label: "Opportunities", icon: Gem },
  { path: "/live", label: "Live Extraction", icon: Radio },
  { path: "/ega", label: "EGA / BriTROC", icon: Dna },
  { path: "/survival", label: "Survival / Cox", icon: Activity },
  { path: "/layers", label: "Layers", icon: LayersIcon },
  { path: "/datasets", label: "Datasets", icon: Database },
  { path: "/signals", label: "Signal Intel", icon: Radar },
  { path: "/bridges", label: "Bridges", icon: GitFork },
  { path: "/gaps", label: "Blind Spots", icon: ShieldAlert },
  { path: "/analyst", label: "Insight Analyst", icon: Sparkles },
  { path: "/value", label: "Value / Moat", icon: Gem },
  { path: "/copilot", label: "Co-Pilot", icon: MessageSquare },
  { path: "/agents", label: "Agents", icon: Bot },
  { path: "/catalog", label: "Catalog", icon: Database },
  { path: "/graph", label: "Graph Explorer", icon: Network },
  { path: "/insights", label: "Insights", icon: Lightbulb },
  { path: "/lineage", label: "Lineage", icon: GitBranch },
  { path: "/query", label: "Query", icon: Terminal },
  { path: "/connectors", label: "Connectors", icon: Plug },
  { path: "/benchmarks", label: "Benchmarks", icon: FlaskConical },
];

function Sidebar() {
  const [location] = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [theme, toggleTheme] = useTheme();

  return (
    <aside
      className={`h-full bg-sidebar border-r border-sidebar-border flex flex-col transition-all duration-200 ${
        collapsed ? "w-[52px]" : "w-[200px]"
      }`}
      data-testid="sidebar"
    >
      <div className="h-12 flex items-center px-3 border-b border-sidebar-border shrink-0">
        <ZetaBridgeLogo collapsed={collapsed} />
      </div>

      <nav className="flex-1 py-2 px-2 space-y-0.5 overflow-y-auto overscroll-contain">
        {navItems.map((item) => {
          const isActive =
            location === item.path ||
            (item.path !== "/" && location.startsWith(item.path));
          const Icon = item.icon;

          return (
            <Link key={item.path} href={item.path}>
              <div
                className={`flex items-center gap-2.5 px-2.5 py-2 rounded-md text-[13px] font-medium cursor-pointer transition-colors ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:text-foreground hover:bg-sidebar-accent"
                }`}
                data-testid={`nav-${item.label.toLowerCase().replace(/\s+/g, "-")}`}
              >
                <Icon
                  className={`w-4 h-4 shrink-0 ${isActive ? "text-primary" : ""}`}
                />
                {!collapsed && <span>{item.label}</span>}
              </div>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-sidebar-border p-2 shrink-0 flex items-center gap-1">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex-1 flex items-center justify-center py-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-sidebar-accent transition-colors"
          data-testid="toggle-sidebar"
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <ChevronLeft className="w-4 h-4" />
          )}
        </button>
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          className="flex items-center justify-center py-1.5 px-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-sidebar-accent transition-colors"
          data-testid="theme-toggle"
        >
          {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>
      </div>
    </aside>
  );
}

function AppLayout() {
  return (
    <div className="h-full flex">
      <Sidebar />
      <main className="flex-1 overflow-y-auto overscroll-contain">
        <Switch>
          <Route path="/" component={DashboardHome} />
          <Route path="/copilot" component={CoPilot} />
          <Route path="/agents" component={AgentsPage} />
          <Route path="/catalog" component={CatalogExplorer} />
          <Route path="/graph" component={GraphExplorer} />
          <Route path="/insights" component={Insights} />
          <Route path="/signals/:slug" component={SignalDetail} />
          <Route path="/signals" component={SignalsHub} />
          <Route path="/opportunities" component={Opportunities} />
          <Route path="/live" component={LiveSources} />
          <Route path="/bridges" component={Bridges} />
          <Route path="/gaps" component={Gaps} />
          <Route path="/analyst" component={Analyst} />
          <Route path="/value" component={Value} />
          <Route path="/lineage" component={LineageGraph} />
          <Route path="/query" component={QueryWorkbench} />
          <Route path="/connectors" component={Connectors} />
          <Route path="/benchmarks" component={Benchmarks} />
          <Route path="/ega" component={EgaBritroc} />
          <Route path="/survival" component={Survival} />
          <Route path="/layers" component={Layers} />
          <Route path="/datasets" component={Datasets} />
          <Route component={NotFound} />
        </Switch>
      </main>
    </div>
  );
}

type Theme = "light" | "dark";

function applyTheme(t: Theme) {
  const el = document.documentElement;
  if (t === "dark") el.classList.add("dark");
  else el.classList.remove("dark");
}

/** Read persisted theme; default to LIGHT (white mode) when nothing is stored. */
function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const stored = window.localStorage.getItem("zeta-theme");
  return stored === "dark" ? "dark" : "light";
}

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  useEffect(() => {
    applyTheme(theme);
    try {
      window.localStorage.setItem("zeta-theme", theme);
    } catch {
      /* ignore storage errors */
    }
  }, [theme]);
  const toggle = () => setTheme((p) => (p === "dark" ? "light" : "dark"));
  return [theme, toggle];
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <Toaster />
        <Router hook={useHashLocation}>
          <AppLayout />
        </Router>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
