import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sankey, sankeyJustify, sankeyLinkHorizontal } from "d3-sankey";
import type { SankeyExtraProperties, SankeyGraph, SankeyLink, SankeyNode } from "d3-sankey";
import type { FlowLinkRaw, FlowNodeRaw } from "./analyticsData";

interface NodeExtra extends SankeyExtraProperties {
  name: string;
  color: string;
  order: number;
}
interface LinkExtra extends SankeyExtraProperties {
  value: number;
}
type SNode = SankeyNode<NodeExtra, LinkExtra>;
type SLink = SankeyLink<NodeExtra, LinkExtra>;

const HEIGHT = 600;
const MIN_WIDTH = 640;
const LINK_OPACITY = 0.32;
const LINK_OPACITY_DIM = 0.06;
const LINK_OPACITY_HOT = 0.6;

/** Tracks the rendered width of a container so the chart can relayout on resize. */
function useContainerWidth() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) setWidth(Math.round(entry.contentRect.width));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export function SankeyChart({ nodes: flowNodes, links: flowLinks }: { nodes: FlowNodeRaw[]; links: FlowLinkRaw[] }) {
  const [wrapRef, measuredWidth] = useContainerWidth();
  // Below MIN_WIDTH the wrapper scrolls horizontally instead of squeezing the diagram.
  const width = Math.max(MIN_WIDTH, measuredWidth || MIN_WIDTH);
  const [activeNode, setActiveNode] = useState<SNode | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; title: string; sub: string } | null>(null);

  const graph: SankeyGraph<NodeExtra, LinkExtra> = useMemo(() => {
    const generator = sankey<NodeExtra, LinkExtra>()
      .nodeAlign(sankeyJustify)
      // Explicit ordering keeps columns in funnel order instead of d3's
      // value-sorted default (Rejected/Offer would jump between stages).
      .nodeSort((a, b) => (a.depth ?? 0) - (b.depth ?? 0) || (a.order ?? 0) - (b.order ?? 0))
      .nodeWidth(16)
      .nodePadding(22)
      .extent([[0, 6], [width, HEIGHT - 6]]);
    return generator({
      nodes: flowNodes.map(node => ({ ...node })),
      links: flowLinks.map(link => ({ ...link })),
    });
  }, [width, flowNodes, flowLinks]);

  const maxDepth = useMemo(() => Math.max(...graph.nodes.map(n => n.depth ?? 0)), [graph]);
  const pathGen = useMemo(() => sankeyLinkHorizontal<SNode, SLink>(), []);

  const moveTip = useCallback((event: React.MouseEvent, title: string, sub: string) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    setTip({
      x: Math.min(event.clientX - rect.left + 14, rect.width - 190),
      y: event.clientY - rect.top + 16,
      title,
      sub,
    });
  }, [wrapRef]);

  const clearTip = useCallback(() => setTip(null), []);

  const linkOpacity = (link: SLink) => {
    if (activeNode) {
      return link.source === activeNode || link.target === activeNode ? LINK_OPACITY_HOT : LINK_OPACITY_DIM;
    }
    return LINK_OPACITY;
  };

  return (
    <div className="an-chart-wrap" ref={wrapRef}>
      <div className="an-chart-scroll" style={{ height: HEIGHT }}>
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label="Sankey diagram of application outcomes: applied and recruiter outreach flow to total applications, then split into no reply, withdrawn, interview stages, rejections, and offers."
        >
          {graph.links.map((link, i) => {
            const source = link.source as SNode;
            const target = link.target as SNode;
            return (
              <path
                key={`link-${i}`}
                d={pathGen(link) || undefined}
                fill="none"
                stroke={source.color}
                strokeWidth={Math.max(1, link.width ?? 1)}
                opacity={linkOpacity(link)}
                onMouseMove={e => moveTip(e, `${source.name} → ${target.name}`, `${link.value} application${link.value === 1 ? "" : "s"}`)}
                onMouseLeave={clearTip}
              >
                <title>{`${source.name} → ${target.name}: ${link.value}`}</title>
              </path>
            );
          })}

          {graph.nodes.map((node, i) => {
            const isSink = (node.depth ?? 0) === maxDepth;
            const labelX = isSink ? (node.x0 ?? 0) - 8 : (node.x1 ?? 0) + 8;
            return (
              <g
                key={`node-${i}`}
                onMouseEnter={() => setActiveNode(node)}
                onMouseMove={e => moveTip(e, node.name, `${node.value ?? 0} application${node.value === 1 ? "" : "s"}`)}
                onMouseLeave={() => { setActiveNode(null); clearTip(); }}
                style={{ cursor: "default" }}
              >
                <rect
                  x={node.x0}
                  y={node.y0}
                  width={Math.max(1, (node.x1 ?? 0) - (node.x0 ?? 0))}
                  height={Math.max(1, (node.y1 ?? 0) - (node.y0 ?? 0))}
                  rx={3}
                  fill={node.color}
                  stroke={activeNode === node ? "var(--ink)" : "none"}
                  strokeWidth={activeNode === node ? 1.5 : 0}
                >
                  <title>{`${node.name}: ${node.value}`}</title>
                </rect>
                <text
                  x={labelX}
                  y={((node.y0 ?? 0) + (node.y1 ?? 0)) / 2}
                  dy="0.32em"
                  textAnchor={isSink ? "end" : "start"}
                  fontSize={11.5}
                  fontWeight={600}
                  fill="var(--ink-2)"
                  stroke="var(--card)"
                  strokeWidth={3}
                  paintOrder="stroke"
                  style={{ pointerEvents: "none", userSelect: "none" }}
                >
                  {node.name}
                  <tspan fill="var(--ink-3)" fontFamily="var(--font-mono)" fontSize={10.5}>{` ${node.value ?? 0}`}</tspan>
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {tip && (
        <div className="an-tooltip" style={{ left: tip.x, top: tip.y }} role="status">
          <strong>{tip.title}</strong>
          <span>{tip.sub}</span>
        </div>
      )}
    </div>
  );
}
