import { BaseEdge, getSmoothStepPath, type Edge, type EdgeProps, type XYPosition } from '@xyflow/react'
import { svgDrawSmoothStepLinePath, useSmartEdgePath } from '@tisoap/react-flow-smart-edge'

const drawReturn = svgDrawSmoothStepLinePath({ borderRadius: 12 })

export type WorkflowEdgeData = { waypoints?: XYPosition[]; returnEdge: boolean; relation?: 'control' | 'input' | 'feedback'; explanation?: string }
export type RoutedWorkflowEdge = Edge<WorkflowEdgeData, 'workflow'>

/** 删除共线的往返段；新线段仍在原路径上，不绕过库计算出的障碍边界。 */
function compactPoints(points: number[][]) {
  const result: number[][] = []
  for (const point of points) {
    const previous = result.at(-1)
    if (previous && previous[0] === point[0] && previous[1] === point[1]) continue
    while (result.length > 1) {
      const a = result[result.length - 2], b = result[result.length - 1]
      if (!((a[0] === b[0] && b[0] === point[0]) || (a[1] === b[1] && b[1] === point[1]))) break
      result.pop()
    }
    result.push(point)
  }
  return result
}

/** 共用异步避障路由，保留原生命中区域、箭头、标签与键盘选择。 */
export function WorkflowEdge(props: EdgeProps<RoutedWorkflowEdge>) {
  const waypoints = props.data?.waypoints
  const { route } = useSmartEdgePath({ ...props, preset: 'smoothstep', waypoints })
  const routed = route?.kind === 'routed'
  const [nativePath, nativeX, nativeY] = getSmoothStepPath({ ...props, borderRadius: 12 })
  const first = waypoints?.[0], last = waypoints?.at(-1)
  // 等待测量或路由不可用时仍能选中/删除；虚线不冒充已经避障的路径。
  const fallback = first && last
    ? `M ${props.sourceX} ${props.sourceY} L ${first.x} ${props.sourceY} L ${first.x} ${first.y} L ${last.x} ${last.y} L ${last.x} ${props.targetY} L ${props.targetX} ${props.targetY}`
    : nativePath
  // 库的途经点绘图为曲线插入控制点；折线直接使用逻辑点，避免角上出现小折返。
  // 外侧走廊距节点至少 48px，把网格吸附产生的末端小台阶收齐到同一水平线。
  const points = routed ? route.points.map(([x, y]) => first && last && x >= last.x - 10 && x <= first.x + 10 && Math.abs(y - first.y) <= 20 ? [x, first.y] : [x, y]) : []
  const path = routed ? waypoints?.length ? drawReturn(
    { x: props.sourceX, y: props.sourceY },
    { x: props.targetX, y: props.targetY }, compactPoints([[props.sourceX, props.sourceY], ...points, [props.targetX, props.targetY]]).slice(1, -1),
  ) : route.svgPathString : fallback
  return <g data-routing={routed ? 'routed' : route ? 'fallback' : 'pending'} data-return-edge={props.data?.returnEdge || undefined} data-relation={props.data?.relation ?? 'control'}>
    {props.data?.explanation && <title>{props.data.explanation}</title>}
    {!routed && <title>连线正在计算或当前间距不足；仍可选择和删除。</title>}
    <BaseEdge id={props.id} path={path}
      markerStart={props.markerStart} markerEnd={props.markerEnd} interactionWidth={24}
      label={props.label} labelX={first && last ? (first.x + last.x) / 2 : routed ? route.edgeCenterX : nativeX}
      labelY={first?.y ?? (routed ? route.edgeCenterY : nativeY)} labelStyle={props.labelStyle}
      labelBgStyle={{ fill: '#f3f9fc', fillOpacity: .96 }} labelBgPadding={[7, 4]} labelBgBorderRadius={5}
      style={{ ...props.style, strokeDasharray: props.data?.relation === 'input' ? '2 6' : props.data?.relation === 'feedback' ? '5 5' : !routed ? '3 5' : props.data?.returnEdge ? '7 4' : undefined }} />
  </g>
}
