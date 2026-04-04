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
  Terminal,
  Plug,
  FlaskConical,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import DashboardHome from "@/pages/dashboard-home";
import CoPilot from "@/pages/copilot";
import AgentsPage from "@/pages/agents";
import CatalogExplorer from "@/pages/catalog-explorer";
import LineageGraph from "@/pages/lineage-graph";
import QueryWorkbench from "@/pages/query-workbench";
import Connectors from "@/pages/connectors";
import Benchmarks from "@/pages/benchmarks";
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
  { path: "/copilot", label: "Co-Pilot", icon: MessageSquare },
  { path: "/agents", label: "Agents", icon: Bot },
  { path: "/catalog", label: "Catalog", icon: Database },
  { path: "/lineage", label: "Lineage", icon: GitBranch },
  { path: "/query", label: "Query", icon: Terminal },
  { path: "/connectors", label: "Connectors", icon: Plug },
  { path: "/benchmarks", label: "Benchmarks", icon: FlaskConical },
];

function Sidebar() {
  const [location] = useLocation();
  const [collapsed, setCollapsed] = useState(false);

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

      <div className="border-t border-sidebar-border p-2 shrink-0">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="w-full flex items-center justify-center py-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-sidebar-accent transition-colors"
          data-testid="toggle-sidebar"
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <ChevronLeft className="w-4 h-4" />
          )}
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
          <Route path="/lineage" component={LineageGraph} />
          <Route path="/query" component={QueryWorkbench} />
          <Route path="/connectors" component={Connectors} />
          <Route path="/benchmarks" component={Benchmarks} />
          <Route component={NotFound} />
        </Switch>
      </main>
    </div>
  );
}

function ThemeInit() {
  useEffect(() => {
    document.documentElement.classList.add("dark");
  }, []);
  return null;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <ThemeInit />
        <Toaster />
        <Router hook={useHashLocation}>
          <AppLayout />
        </Router>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
