/**
 * ProvenanceBadge (Session 15) — a small, reusable "trace" chip that makes every
 * displayed value honest about where it came from.
 *
 * It shows: the source endpoint (with its color dot), whether the value is
 * `live` (pulled just now from the source system) or `snapshot` (from the
 * pre-extracted graph export), and the grounding handle that produced it — a
 * graph node id, or a live query descriptor (e.g. "ega:list_files").
 *
 * Used on the Opportunity cards, the signal-hub headline, and the Live Console
 * so a viewer can always answer "is this real, and where did it come from?".
 */

import { Radio, Database } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ENDPOINT_META, type EndpointCode } from "@/lib/graphApi";

export type Provenance = "live" | "snapshot";

function epColor(ep: EndpointCode): string {
  return ep && ep in ENDPOINT_META ? ENDPOINT_META[ep].color : "hsl(220,20%,55%)";
}
function epShort(ep: EndpointCode): string {
  return ep && ep in ENDPOINT_META ? ENDPOINT_META[ep].short : "?";
}

export function ProvenanceBadge({
  endpoint,
  provenance,
  handle,
  className,
}: {
  /** Which source endpoint produced the value. */
  endpoint: EndpointCode;
  /** Whether the value is a live source read or a snapshot (graph export) value. */
  provenance: Provenance;
  /** The grounding handle — a graph node id OR a live query descriptor. */
  handle?: string | null;
  className?: string;
}) {
  const Icon = provenance === "live" ? Radio : Database;
  const epLabel =
    endpoint && endpoint in ENDPOINT_META ? ENDPOINT_META[endpoint].label : "unknown endpoint";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className={`gap-1.5 font-mono text-[10px] cursor-help ${className ?? ""}`}
          data-testid={`provenance-${provenance}`}
        >
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: epColor(endpoint) }}
          />
          <span className="font-semibold">{epShort(endpoint)}</span>
          <Icon
            className={`w-3 h-3 ${provenance === "live" ? "text-emerald-400" : "text-muted-foreground"}`}
          />
          <span className={provenance === "live" ? "text-emerald-400" : "text-muted-foreground"}>
            {provenance}
          </span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs">
        <p className="text-xs">
          <span className="font-semibold">{epLabel}</span>
          <br />
          {provenance === "live"
            ? "Pulled just now from the live source system."
            : "From the pre-extracted federated graph export."}
          {handle ? (
            <>
              <br />
              <span className="font-mono text-[10px] text-muted-foreground break-all">
                {handle}
              </span>
            </>
          ) : null}
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

export default ProvenanceBadge;
