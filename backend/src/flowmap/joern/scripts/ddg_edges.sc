import scala.collection.mutable

val __ddgInput = ujson.read(__QUESTIONS_JSON_STRING__)
val __maxDepth = __ddgInput("maxDepth").num.toInt
val __sourcesByTarget: Map[Long, Set[Long]] =
  __ddgInput("sourcesByTarget").obj.map { case (targetId, sourceValues) =>
    targetId.stripPrefix("c").toLong -> sourceValues.arr
      .map(_.str.stripPrefix("c").toLong).toSet
  }.toMap
val __retainedCallIds: Set[Long] =
  __sourcesByTarget.keySet ++ __sourcesByTarget.values.flatten

val __targetsById = cpg.call
  .filter(call => __sourcesByTarget.contains(call.id))
  .l.map(call => call.id -> call).toMap
val __edges = mutable.LinkedHashSet[(String, String)]()
var __visitedNodes = 0
var __maxVisitedForTarget = 0

__targetsById.foreach { case (targetId, target) =>
  val allowedSources = __sourcesByTarget(targetId)
  // Explicit breadth-first traversal avoids repeat-path explosion: a DDG
  // node is visited at most once for this target, even across diamonds and
  // cycles. Pass through raw/noisy calls; stop a path at the nearest call
  // retained by the filtered operation graph.
  val seen = mutable.Set[Long](target.id)
  var frontier: List[CfgNode] = List(target)
  var depth = 0

  while (frontier.nonEmpty && depth < __maxDepth) {
    val next = mutable.ArrayBuffer[CfgNode]()
    frontier.foreach { node =>
      node.start.ddgIn.l.foreach { predecessor =>
        if (!seen.contains(predecessor.id)) {
          seen += predecessor.id
          predecessor match {
            case call: Call if __retainedCallIds.contains(call.id) =>
              if (allowedSources.contains(call.id)) {
                __edges += ((s"c${call.id}", s"c${target.id}"))
              }
            case other => next += other
          }
        }
      }
    }
    frontier = next.toList
    depth += 1
  }

  __visitedNodes += seen.size
  __maxVisitedForTarget = math.max(__maxVisitedForTarget, seen.size)
}

val __missingTargets = __sourcesByTarget.keySet -- __targetsById.keySet
val __ddgResult = ujson.Obj(
  "edges" -> ujson.Arr(__edges.toList.map { case (source, target) =>
    ujson.Obj("from" -> source, "to" -> target, "type" -> "data")
  }*),
  "stats" -> ujson.Obj(
    "candidatePairs" -> __sourcesByTarget.values.map(_.size).sum,
    "targetsRequested" -> __sourcesByTarget.size,
    "targetsQueried" -> __targetsById.size,
    "confirmedEdges" -> __edges.size,
    "visitedNodes" -> __visitedNodes,
    "maxVisitedForTarget" -> __maxVisitedForTarget,
    "missingTargets" -> ujson.Arr(__missingTargets.toList.sorted.map(id => ujson.Str(s"c$id"))*),
  )
)

val __ddgJson: String = ujson.write(__ddgResult, indent = 2)
