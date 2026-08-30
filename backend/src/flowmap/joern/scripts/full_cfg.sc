import scala.collection.mutable


def buildFullCodebaseCfg(): ujson.Obj = {
  val nodes = mutable.LinkedHashMap[String, ujson.Obj]()
  val edges = mutable.ArrayBuffer[ujson.Obj]()
  val branchGroups = mutable.ArrayBuffer[ujson.Obj]()
  // Structural route facts are kept internal until package 12 derives every
  // serialized edge requirement in one authoritative post-normalization
  // pass. They cover arm-selection edges and pending completion resumed after
  // finally; ordinary edges derive requirements solely from source ownership.
  val structuralRouteRequirements = mutable.LinkedHashMap[
    (String, String), mutable.ArrayBuffer[List[(String, String)]]
  ]()

  def recordStructuralRouteRequirements(
    source: String,
    target: String,
    requirements: Iterable[(String, String)]
  ): Unit = {
    val alternative = requirements.toList.distinct
    if (alternative.nonEmpty) {
      val stored = structuralRouteRequirements.getOrElseUpdate(
        (source, target), mutable.ArrayBuffer[List[(String, String)]]()
      )
      if (!stored.contains(alternative)) stored += alternative
    }
  }
  val loopGroups = mutable.ArrayBuffer[ujson.Obj]()
  val semanticFeatures = ujson.Obj()

  // (groupId, armLabel) pairs per retained CFG-node id. Calls use these for
  // operation membership; RETURN/throw exits use them as authoritative route
  // requirements even when an arm contains no calls.
  type ArmTags = mutable.Map[Long, mutable.ArrayBuffer[(String, String)]]
  type LoopTags = mutable.Map[Long, mutable.ArrayBuffer[String]]

  // Internal discovery model. These descriptions keep lexical ownership and
  // raw CFG boundary facts separate from the later anchor-normalization pass.
  case class StructureRegion(
    groupId: String,
    kind: String,
    rootAstNodeId: Long,
    astNodeIds: Set[Long],
    parentGroupIds: List[String],
    entryAnchorId: String,
    exitAnchorId: String
  )

  case class ArmRegion(
    label: String,
    astNodeIds: Set[Long],
    entryCfgNodeIds: Set[Long]
  )

  case class ConditionStageRegion(
    id: String,
    nodeIds: Set[Long],
    entryCfgNodeIds: Set[Long],
    decisionAnchorId: String
  )

  case class BranchRegion(
    structure: StructureRegion,
    arms: List[ArmRegion],
    conditionStages: List[ConditionStageRegion]
  )

  case class LoopRegion(
    structure: StructureRegion,
    bodyNodeIds: Set[Long],
    guardNodeIds: Set[Long],
    initializerNodeIds: Set[Long],
    updateNodeIds: Set[Long],
    bodyEntryNodeIds: Set[Long],
    continuationCfgNodeIds: Set[Long]
  )

  val discoveredBranchRegions = mutable.ArrayBuffer[BranchRegion]()
  val discoveredLoopRegions = mutable.ArrayBuffer[LoopRegion]()

  case class CandidateCfgEdge(
    sourceNodeId: Long,
    targetNodeId: Long,
    cycleClosing: Boolean
  )

  case class CandidateMethodCfg(
    methodId: Long,
    methodFullName: String,
    nodesById: Map[Long, CfgNode],
    edges: List[CandidateCfgEdge]
  )

  val discoveredCandidateCfgs = mutable.ArrayBuffer[CandidateMethodCfg]()

  case class IfStagePlan(
    id: String,
    decisionNodeId: String,
    conditionCallIds: Set[String],
    conditionExecutionIds: List[String],
    conditionEntryIds: List[String],
    conditionExitIds: List[String]
  )

  case class IfArmPlan(
    label: String,
    entryIds: List[String],
    terminalIds: List[String],
    continues: Boolean
  )

  case class IfAnchorPlan(
    groupId: String,
    astSize: Int,
    entryNodeId: String,
    exitNodeId: Option[String],
    stages: List[IfStagePlan],
    arms: List[IfArmPlan],
    continuationIds: List[String],
    enclosingBranchArms: List[(String, String)],
    enclosingLoopIds: List[String]
  )

  val pendingIfAnchorPlans = mutable.ArrayBuffer[IfAnchorPlan]()

  case class TryArmPlan(
    label: String,
    astNodeIds: Set[Long],
    projectedNodeIds: Set[String],
    entryIds: List[String]
  )

  case class TryAnchorPlan(
    groupId: String,
    astSize: Int,
    entryNodeId: String,
    decisionNodeId: String,
    exitNodeId: String,
    tryBodyAstNodeIds: Set[Long],
    tryBodyNodeIds: Set[String],
    tryBodyEntryIds: List[String],
    catchArms: List[TryArmPlan],
    finallyAstNodeIds: Set[Long],
    finallyNodeIds: Set[String],
    continuationIds: List[String],
    enclosingBranchArms: List[(String, String)],
    enclosingLoopIds: List[String]
  )

  val pendingTryAnchorPlans = mutable.ArrayBuffer[TryAnchorPlan]()

  case class LexicalOwnership(
    branchArms: List[(String, String)],
    loopIds: List[String]
  )

  def serializedBranchRequirements(
    requirements: List[(String, String)]
  ): ujson.Arr = ujson.Arr(requirements.distinct.map {
    case (groupId, armLabel) => ujson.Obj(
      "groupId" -> ujson.Str(groupId),
      "armLabel" -> ujson.Str(armLabel)
    )
  }*)

  def lexicalOwnershipAt(
    nodeId: Long,
    branchTags: ArmTags,
    loopTags: LoopTags,
    ownGroupId: Option[String] = None
  ): LexicalOwnership = LexicalOwnership(
    branchTags.getOrElse(nodeId, mutable.ArrayBuffer()).distinct.toList
      .filterNot(tag => ownGroupId.contains(tag._1)),
    loopTags.getOrElse(nodeId, mutable.ArrayBuffer()).distinct.toList
      .filterNot(ownGroupId.contains)
  )

  def writeLexicalOwnership(
    target: ujson.Obj,
    ownership: LexicalOwnership
  ): Unit = {
    if (ownership.branchArms.nonEmpty) {
      target("branchArms") = serializedBranchRequirements(ownership.branchArms)
    }
    if (ownership.loopIds.nonEmpty) {
      target("loopIds") = ujson.Arr(ownership.loopIds.distinct.map(ujson.Str(_))*)
    }
  }

  case class LoopAnchorPlan(
    groupId: String,
    kind: String,
    bodyNodeIds: Set[String],
    guardNodeIds: Set[String],
    updateNodeIds: Set[String],
    entryInitialIds: Set[String],
    continuationIds: Set[String],
    entryNodeId: String,
    exitNodeId: String,
    astSize: Int
  )

  val pendingLoopAnchorPlans = mutable.ArrayBuffer[LoopAnchorPlan]()

  case class StructuralTransferPlan(nodeId: String, targetGroupId: String)
  val pendingStructuralTransfers = mutable.ArrayBuffer[StructuralTransferPlan]()

  case class FallthroughPlan(
    nodeId: String,
    callerMethod: String,
    sourceFile: String,
    line: Int
  )
  val pendingFallthroughPlans = mutable.ArrayBuffer[FallthroughPlan]()

  def cfgNodeIds(root: AstNode): Set[Long] =
    root.start.ast.l.collect { case node: CfgNode => node.id }.toSet

  def cfgEntryNodeIds(astNodeIds: Set[Long], roots: List[AstNode]): Set[Long] =
    roots.flatMap(_.start.ast.l.collect { case node: CfgNode => node })
      .filter(node => node.start.cfgPrev.l.exists(previous => !astNodeIds.contains(previous.id)))
      .map(_.id).toSet

  def lexicalParentGroupIds(
    node: AstNode,
    groupIdByControlStructureId: Map[Long, String]
  ): List[String] = {
    val parents = mutable.ArrayBuffer[String]()
    var current = node
    var walking = true
    while (walking) {
      current.start.astParent.headOption match {
        case Some(parent: ControlStructure) =>
          groupIdByControlStructureId.get(parent.id).foreach(parents.prepend(_))
          current = parent
        case Some(parent) => current = parent
        case None => walking = false
      }
    }
    parents.distinct.toList
  }

  def discoverLoopRegion(
    structure: ControlStructure,
    groupIdByControlStructureId: Map[Long, String]
  ): LoopRegion = {
    val groupId = s"loop${structure.id}"
    val allAstNodes = structure.start.ast.l
    val loopAstIds = allAstNodes.map(_.id).toSet
    val blocks = structure.astChildren.l.filter(_.label == "BLOCK")
    val bodyRoots = blocks.lastOption.toList
    val bodyAstIds = bodyRoots.flatMap(_.start.ast.l.map(_.id)).toSet
    val guardIds = structure.condition.headOption.toList
      .flatMap(_.start.ast.l.map(_.id)).toSet
    val initializerIds = blocks.dropRight(1)
      .flatMap(_.start.ast.l.map(_.id)).toSet
    val structuralIds = bodyAstIds ++ guardIds ++ initializerIds + structure.id
    val updateIds = structure.astChildren.l
      .filterNot(child => structuralIds.contains(child.id))
      .flatMap(_.start.ast.l.map(_.id)).toSet
    val continuationIds = allAstNodes.collect { case node: CfgNode => node }
      .flatMap(_.start.cfgNext.l)
      .filterNot(next => loopAstIds.contains(next.id))
      .map(_.id).toSet
    LoopRegion(
      StructureRegion(
        groupId,
        structure.controlStructureType,
        structure.id,
        loopAstIds,
        lexicalParentGroupIds(structure, groupIdByControlStructureId),
        s"$groupId:entry",
        s"$groupId:exit"
      ),
      bodyAstIds,
      guardIds,
      initializerIds,
      updateIds,
      cfgEntryNodeIds(bodyAstIds, bodyRoots),
      continuationIds
    )
  }

  def addNode(id: String, obj: ujson.Obj): Unit = {
    if (!nodes.contains(id)) nodes(id) = obj
  }

  // --- SECTION: SEMANTIC FEATURE EXTRACTION ---

  def stringArray(values: Iterable[String]): ujson.Arr =
    ujson.Arr(values.toList.distinct.map(ujson.Str(_))*)

  // Extracts candidate Java types from a receiver or argument expression for 
  // semanticFeature
  def expressionTypes(expression: Expression): List[String] = (
    expression.start.isCall.typeFullName.l ++
      expression.start.ast.isIdentifier.typeFullName.l ++
      expression.start.ast.isLiteral.typeFullName.l ++
      expression.start.ast.isCall.typeFullName.l
  ).filter(_.nonEmpty).distinct

  // Best-effort semantic evidence for one call site.
  def semanticFeature(call: Call): ujson.Obj = {
    def readable(code: String): Boolean = code != null && code.nonEmpty && code != "<empty>"
    def meaningfulName(name: String): Boolean =
      name.nonEmpty && !name.startsWith("$") && name != "this" && name != "super"
    def usefulType(t: String): Boolean =
      t.nonEmpty && t != "<empty>" && t != "ANY"

    val orderedArguments = call.argument.l.sortBy(_.argumentIndex)
    val receiver = orderedArguments.find(_.argumentIndex == 0)
    val explicitArguments = orderedArguments.filter(_.argumentIndex > 0)
    val argumentCodes = explicitArguments.map(_.code).filter(readable)
    val argumentsObserved = explicitArguments.forall(argument => readable(argument.code))
    val identifiers = explicitArguments.flatMap(
      argument => argument.start.ast.isIdentifier.name.l
    ).filter(meaningfulName).distinct
    val argumentFields = explicitArguments.flatMap(
      argument => argument.start.ast.isFieldIdentifier.canonicalName.l
    ).distinct

    val calleeEntries = call.callee.internal.l
    val assignmentNames = Set(
      "<operator>.assignment",
      "<operator>.assignmentPlus",
      "<operator>.assignmentMinus",
      "<operator>.assignmentMultiplication",
      "<operator>.assignmentDivision"
    )
    val writtenFields = calleeEntries.flatMap { callee =>
      callee.ast.isCall.l
        .filter(assignment => assignmentNames.contains(assignment.name))
        .flatMap(_.argument.l.filter(_.argumentIndex == 1))
        .flatMap(lhs => lhs.start.ast.isFieldIdentifier.canonicalName.l)
    }.distinct
    val calleeFields = calleeEntries.flatMap(
      callee => callee.ast.isFieldIdentifier.canonicalName.l
    ).distinct.filterNot(writtenFields.contains)

    val receiverCode = receiver.map(_.code).filter(readable)
    val receiverType = receiver.flatMap(expressionTypes(_).headOption).filter(usefulType)
    val argumentTypes = explicitArguments.flatMap(expressionTypes).filter(usefulType)
    val outputType = Option(call.typeFullName).filter(usefulType)
    val domainTypes = (
      receiverType.toList ++ argumentTypes ++ outputType.toList
    ).filterNot(t => Set("void", "java.lang.Object").contains(t)).distinct
    val methodTerms = call.name
      .split("(?<=[a-z0-9])(?=[A-Z])|[^A-Za-z0-9]+")
      .map(_.toLowerCase).filter(_.nonEmpty).toList

    val observed =
      (if (receiver.isEmpty || receiverCode.isDefined) List("receiver") else Nil) ++
      (if (argumentsObserved) List("arguments", "inputs") else Nil) ++
      List("callsiteFields") ++
      (if (outputType.isDefined) List("output") else Nil) ++
      (if (calleeEntries.nonEmpty) List("calleeFields") else Nil)

    val result = ujson.Obj(
      "arguments" -> stringArray(argumentCodes),
      "argumentTypes" -> stringArray(argumentTypes),
      "inputIdentifiers" -> stringArray(identifiers),
      "fieldsRead" -> stringArray((argumentFields ++ calleeFields).distinct),
      "fieldsWritten" -> stringArray(writtenFields),
      "domainTypes" -> stringArray(domainTypes),
      "methodTerms" -> stringArray(methodTerms),
      "observedFeatures" -> stringArray(observed)
    )
    receiverCode.foreach(code => result("receiver") = ujson.Str(code))
    receiverType.foreach(t => result("receiverType") = ujson.Str(t))
    outputType.foreach(t => result("outputType") = ujson.Str(t))
    result
  }


  // --- SECTION: LAMBDA FUNCTION RESOLUTION ---

  // For a method with a missing filename (Joern declaring <empty>), use 
  // filename of the class owning the method if available. 
  def methodSourceFile(method: Method): String = {
    val direct = method.filename
    if (direct.nonEmpty && direct != "<empty>") direct
    else method.typeDecl.filename.headOption.getOrElse(direct)
  }

  // Presentation uses a deliberately non-short-circuit condition contract:
  // evaluate every projected operand call from left to right, then its
  // enclosing operator. AST post-order supplies that stable order even when
    // Joern's execution-accurate CFG contains &&/|| shortcut edges.
  def conditionExecutionCallsOf(structure: ControlStructure): List[Call] = {
    def postOrder(node: AstNode): List[Call] =
      node.start.astChildren.l.sortBy(_.order).flatMap(postOrder) ++ (node match {
        case call: Call => List(call)
        case _ => Nil
      })
    structure.condition.headOption.toList
      .flatMap(postOrder).distinctBy(_.id)
  }

  // Project a physical CFG continuation to the first node ids represented in
  // the extracted graph. Unlike nearestCalls this also preserves a direct
  // return or method fallthrough destination.
  def nearestProjectedIds(startPoints: List[CfgNode]): List[String] = {
    val seen = mutable.Set[Long]()
    val found = mutable.LinkedHashSet[String]()
    val pending = mutable.Queue.from(startPoints.distinctBy(_.id))
    while (pending.nonEmpty) {
      val node = pending.dequeue()
      if (!seen.contains(node.id)) {
        seen += node.id
        node match {
          case call: Call => found += s"c${call.id}"
          case _ if node.label == "RETURN" => found += s"r${node.id}"
          case _ if node.label == "METHOD_RETURN" => found += s"f${node.id}"
          case _ => node.start.cfgNext.l.foreach(pending.enqueue(_))
        }
      }
    }
    found.toList
  }

  /** Resolve the first presentation boundary of a structural body.
    *
    * Ordinary projection is deliberately call-oriented and may walk through
    * an entire nested IF/loop before finding a retained call. Structural
    * composition must stop earlier: at the entry anchor of the immediate
    * child crossed by the raw CFG. All regions and anchors have already been
    * discovered when this is called, so targeting a not-yet-normalized anchor
    * is safe. The child exclusively owns entry -> internals -> exit.
    */
  def structuralBodyEntryIds(
    bodyRoot: AstNode,
    rawEntryNodes: List[CfgNode],
    owningGroupId: String,
    allowedProjectedIds: Set[String]
  ): List[String] = {
    val bodyAstOrder = bodyRoot.start.ast.l.zipWithIndex.map {
      case (node, index) => node.id -> index
    }.toMap
    val bodyAstIds = bodyAstOrder.keySet
    val structures = (
      discoveredBranchRegions.map(_.structure) ++
        discoveredLoopRegions.map(_.structure)
    ).filter(structure =>
      structure.groupId != owningGroupId &&
        bodyAstIds.contains(structure.rootAstNodeId)
    ).toList
    // Keep only immediate children. A grandchild root is contained by another
    // candidate child and must never become a direct parent target.
    val immediateChildren = structures.filterNot { candidate =>
      structures.exists(other =>
        other.groupId != candidate.groupId &&
          other.astNodeIds.contains(candidate.rootAstNodeId)
      )
    }
    val crossedChildren = immediateChildren.filter { child =>
      rawEntryNodes.exists(node => child.astNodeIds.contains(node.id))
    }.sortBy(child => bodyAstOrder.getOrElse(child.rootAstNodeId, Int.MaxValue))

    val projected = nearestProjectedIds(rawEntryNodes)
      .filter(allowedProjectedIds.contains)
    val projectedAstIndex = projected.flatMap { nodeId =>
      nodeId.drop(1).toLongOption.flatMap(bodyAstOrder.get)
    }.minOption.getOrElse(Int.MaxValue)
    val earliestChild = immediateChildren.sortBy(child =>
      bodyAstOrder.getOrElse(child.rootAstNodeId, Int.MaxValue)
    ).headOption
    val structuralTarget = crossedChildren.headOption.orElse {
      // Joern can expose a shortcut directly to work after a call-free child
      // condition. AST containment only disambiguates which already-discovered
      // gateway occurs before that CFG-projected call; it does not order calls.
      earliestChild.filter(child =>
        bodyAstOrder.getOrElse(child.rootAstNodeId, Int.MaxValue) < projectedAstIndex
      )
    }

    // Several raw shortcuts can enter later statements of the same body. The
    // static presentation contract shows the most complete lexical route, so
    // the earliest crossed immediate child is the one mandatory gateway.
    structuralTarget match {
      case Some(child) => List(child.entryAnchorId)
      case None =>
        if (projected.nonEmpty) projected
        else {
          // Exceptional fallback only: Joern can expose no usable entry for a
          // duplicated/lowered finally body. Preserve Java evaluation order
          // with AST post-order and take the first retained operation.
          def postOrder(node: AstNode): List[Call] =
            node.start.astChildren.l.sortBy(_.order).flatMap(postOrder) ++ (node match {
              case call: Call => List(call)
              case _ => Nil
            })
          postOrder(bodyRoot)
            .map(call => s"c${call.id}")
            .filter(allowedProjectedIds.contains)
            .take(1)
        }
    }
  }

  // Project every raw CFG crossing from a lexical structure to the first
  // retained graph node. This is authoritative for ordinary executable
  // sequencing; AST supplies only the lexical region boundary.
  def projectedCfgBoundaryContinuationIds(
    structureRoot: AstNode,
    structureAstIds: Set[Long]
  ): List[String] = {
    val boundaryStarts = structureRoot.start.ast.l.collect {
      case node: CfgNode => node
    }.flatMap(_.start.cfgNext.l)
      .filterNot(next => structureAstIds.contains(next.id))
      .distinctBy(_.id)
    nearestProjectedIds(boundaryStarts)
  }

  // Exceptional presentation fallback for a structure whose raw CFG exposes
  // no usable boundary route (most notably a syntactically empty arm). AST may
  // identify the structural continuation, but never orders ordinary calls.
  // A following supported structure is entered through its entry anchor.
  def exceptionalAstStructuralContinuationIds(node: AstNode): List[String] = {
    var current = node
    var nextRoot: Option[AstNode] = None
    var searching = true
    while (searching) {
      current.start.astParent.headOption match {
        case None => searching = false
        case Some(parent) =>
          val siblings = parent.astChildren.l
          val index = siblings.indexWhere(_.id == current.id)
          val isControlArm = parent match {
            case structure: ControlStructure
                if structure.controlStructureType == "IF" =>
              !structure.condition.headOption.exists(_.id == current.id)
            case _ => false
          }
          // Sibling AST children of an IF are mutually-exclusive arms, not
          // sequential continuations. When leaving a nested IF at the end of
          // an outer arm, climb over the whole enclosing IF before looking
          // for the next statement.
          if (isControlArm) {
            current = parent
          } else if (index >= 0 && index + 1 < siblings.size) {
            nextRoot = Some(siblings(index + 1))
            searching = false
          } else {
            current = parent
          }
      }
    }
    nextRoot.toList.flatMap {
      case structure: ControlStructure
          if Set("IF", "TRY").contains(structure.controlStructureType) =>
        List(s"cs${structure.id}:entry")
      case structure: ControlStructure
          if Set("WHILE", "DO", "DO_WHILE", "FOR").contains(
            structure.controlStructureType
          ) =>
        List(s"loop${structure.id}:entry")
      case root =>
        val projected = root.ast.l.collect {
          case call: Call => call: CfgNode
          case ret: Return => ret: CfgNode
        }
        val projectedIds = projected.map(_.id).toSet
        val entries = projected.filter { candidate =>
          candidate.start.cfgPrev.l.forall(previous => !projectedIds.contains(previous.id))
        }
        nearestProjectedIds(entries)
    }.distinct
  }

  // List all internal methods for purpose of resolving lambdas. 
  val internalMethodsByFullName: Map[String, List[Method]] =
    cpg.method.isExternal(false).whereNot(_.isAbstract).l.groupBy(_.fullName)

  def isLambdaImplementation(method: Method): Boolean =
    method.name.matches("(?:lambda\\$.*\\$\\d+|<lambda>\\d+)")

  // Bridge METHOD REF to actual lambda method implementation
  def referencedInternalMethods(call: Call): List[Method] =
    call.argument.isMethodRef.l.flatMap { ref =>
      internalMethodsByFullName.getOrElse(ref.methodFullName, Nil)
    }.filter(isLambdaImplementation).distinctBy(_.id)


  // --- SECTION: CREATE BRANCH GROUP AND DETAILS ---

  // Flatten a chain of nested IFs into ONE group with N mutually-exclusive
  // arms, instead of N nested groups.
  def elseChainNext(cs: ControlStructure): Option[ControlStructure] = {
    val conditionId = cs.condition.headOption.map(_.id)
    val armRoots = cs.astChildren.l.filterNot(c => conditionId.contains(c.id))
    if (armRoots.size < 2) return None
    def asIf(n: AstNode): Option[ControlStructure] = n match {
      case c: ControlStructure if c.controlStructureType == "IF" => Some(c)
      case _                                                     => None
    }
    def statementsOf(n: AstNode): List[AstNode] = {
      val kids = n.astChildren.l
      if (kids.size == 1 && kids.head.label == "BLOCK") kids.head.astChildren.l else kids
    }
    val elseArm = armRoots.last
    asIf(elseArm).orElse {
      val statements = statementsOf(elseArm)
      if (statements.size == 1) asIf(statements.head) else None
    }
  }

  def discoverBranchRegion(
    head: ControlStructure,
    groupIdByControlStructureId: Map[Long, String]
  ): BranchRegion = {
    val groupId = s"cs${head.id}"
    val arms = mutable.ArrayBuffer[ArmRegion]()
    val stages = mutable.ArrayBuffer[ConditionStageRegion]()
    var current = head
    var index = 0
    var walking = true
    while (walking) {
      val condition = current.condition.headOption
      val conditionId = condition.map(_.id)
      val armRoots = current.astChildren.l.filterNot(child => conditionId.contains(child.id))
      val label = if (index == 0) "if" else s"elseif$index"
      armRoots.headOption.foreach { root =>
        val ids = cfgNodeIds(root)
        arms += ArmRegion(label, root.start.ast.l.map(_.id).toSet, cfgEntryNodeIds(ids, List(root)))
      }
      val conditionNodeIds = condition.toList.flatMap(
        _.start.ast.l.collect { case node: CfgNode => node.id }
      ).toSet
      val conditionEntries = condition.toList.flatMap { conditionRoot =>
        val conditionNodes = conditionRoot.start.ast.l.collect { case node: CfgNode => node }
        conditionNodes.filter { node =>
          node.start.cfgPrev.l.exists(previous => !conditionNodeIds.contains(previous.id))
        }.map(_.id)
      }.toSet
      stages += ConditionStageRegion(
        s"$groupId:condition:$index",
        conditionNodeIds,
        conditionEntries,
        // Every else-if stage owns one canonical structural decision.
        s"b${current.id}"
      )
      elseChainNext(current) match {
        case Some(next) =>
          current = next
          index += 1
        case None =>
          if (armRoots.size > 1) {
            val root = armRoots.last
            val ids = cfgNodeIds(root)
            arms += ArmRegion("else", root.start.ast.l.map(_.id).toSet, cfgEntryNodeIds(ids, List(root)))
          } else {
            arms += ArmRegion("else", Set.empty, Set.empty)
          }
          walking = false
      }
    }
    BranchRegion(
      StructureRegion(
        groupId,
        "IF",
        head.id,
        head.start.ast.l.map(_.id).toSet,
        lexicalParentGroupIds(head, groupIdByControlStructureId),
        s"$groupId:entry",
        s"$groupId:exit"
      ),
      arms.toList,
      stages.toList
    )
  }

  def discoverTryRegion(
    structure: ControlStructure,
    groupIdByControlStructureId: Map[Long, String]
  ): BranchRegion = {
    val groupId = s"cs${structure.id}"
    var catchIndex = 0
    val catchArms = structure.astChildren.l.flatMap {
      case catchNode: ControlStructure if catchNode.controlStructureType == "CATCH" =>
        catchIndex += 1
        val astIds = catchNode.start.ast.l.map(_.id).toSet
        Some(ArmRegion(
          s"catch$catchIndex",
          astIds,
          cfgEntryNodeIds(astIds, List(catchNode))
        ))
      case _ => None
    }
    BranchRegion(
      StructureRegion(
        groupId,
        "TRY",
        structure.id,
        structure.start.ast.l.map(_.id).toSet,
        lexicalParentGroupIds(structure, groupIdByControlStructureId),
        s"$groupId:entry",
        s"$groupId:exit"
      ),
      catchArms :+ ArmRegion("noCatch", Set.empty, Set.empty),
      Nil
    )
  }

  def deriveLexicalOwnership(
    branchRegions: List[BranchRegion],
    loopRegions: List[LoopRegion]
  ): (ArmTags, LoopTags, Map[String, LexicalOwnership]) = {
    val branchTags: ArmTags = mutable.Map()
    val loopTags: LoopTags = mutable.Map()

    // Larger AST regions are outer scopes.  Stable outer-to-inner insertion
    // makes serialized membership deterministic without inferring hierarchy
    // from CFG edges.
    branchRegions.sortBy(region => -region.structure.astNodeIds.size).foreach { region =>
      region.arms.foreach { arm =>
        arm.astNodeIds.foreach { nodeId =>
          branchTags.getOrElseUpdate(nodeId, mutable.ArrayBuffer()) +=
            ((region.structure.groupId, arm.label))
        }
      }
    }
    loopRegions.sortBy(region => -region.structure.astNodeIds.size).foreach { region =>
      val repeatedNodeIds =
        region.bodyNodeIds ++ region.guardNodeIds ++ region.updateNodeIds
      repeatedNodeIds.foreach { nodeId =>
        loopTags.getOrElseUpdate(nodeId, mutable.ArrayBuffer()) +=
          region.structure.groupId
      }
    }

    val structuralOwnership = mutable.LinkedHashMap[String, LexicalOwnership]()
    branchRegions.foreach { region =>
      val structure = region.structure
      val enclosure = lexicalOwnershipAt(
        structure.rootAstNodeId, branchTags, loopTags, Some(structure.groupId)
      )
      structuralOwnership(structure.entryAnchorId) = enclosure
      structuralOwnership(structure.exitAnchorId) = enclosure
      region.conditionStages.foreach { stage =>
        structuralOwnership(stage.decisionAnchorId) = enclosure
      }
    }
    loopRegions.foreach { region =>
      val structure = region.structure
      val enclosure = lexicalOwnershipAt(
        structure.rootAstNodeId, branchTags, loopTags, Some(structure.groupId)
      )
      structuralOwnership(structure.entryAnchorId) = enclosure
      structuralOwnership(structure.exitAnchorId) = enclosure
    }
    (branchTags, loopTags, structuralOwnership.toMap)
  }

  def validateLexicalOwnership(
    branchRegions: List[BranchRegion],
    loopRegions: List[LoopRegion],
    branchTags: ArmTags,
    loopTags: LoopTags,
    structuralOwnership: Map[String, LexicalOwnership]
  ): Unit = {
    (branchRegions.map(_.structure) ++ loopRegions.map(_.structure)).foreach { structure =>
      val entry = structuralOwnership(structure.entryAnchorId)
      val exit = structuralOwnership(structure.exitAnchorId)
      if (entry != exit) {
        throw new IllegalStateException(
          s"Structure ${structure.groupId} entry/exit ownership differs: $entry != $exit"
        )
      }
      if (entry.branchArms.exists(_._1 == structure.groupId) ||
          entry.loopIds.contains(structure.groupId)) {
        throw new IllegalStateException(
          s"Structure ${structure.groupId} anchor contains its own membership: $entry"
        )
      }
    }
    branchRegions.foreach { region =>
      region.arms.foreach { arm =>
        arm.astNodeIds.foreach { nodeId =>
          val expected = (region.structure.groupId, arm.label)
          if (!branchTags.getOrElse(nodeId, mutable.ArrayBuffer()).contains(expected)) {
            throw new IllegalStateException(
              s"Node $nodeId is missing lexical branch membership $expected"
            )
          }
        }
      }
    }
    loopRegions.foreach { region =>
      val expected = region.structure.groupId
      val repeatedIds = region.bodyNodeIds ++ region.guardNodeIds ++ region.updateNodeIds
      repeatedIds.foreach { nodeId =>
        if (!loopTags.getOrElse(nodeId, mutable.ArrayBuffer()).contains(expected)) {
          throw new IllegalStateException(
            s"Node $nodeId is missing lexical loop membership $expected"
          )
        }
      }
    }
  }

  def discoverCandidateCfg(
    method: Method,
    entryStartPoints: List[CfgNode]
  ): CandidateMethodCfg = {
    val nodesById = mutable.LinkedHashMap[Long, CfgNode]()
    val edgesByPair = mutable.LinkedHashMap[(Long, Long), CandidateCfgEdge]()
    val globallyExpanded = mutable.Set[Long]()

    def record(sourceId: Long, targetId: Long, cycleClosing: Boolean): Unit = {
      val key = (sourceId, targetId)
      edgesByPair.get(key) match {
        case Some(existing) if cycleClosing && !existing.cycleClosing =>
          edgesByPair(key) = existing.copy(cycleClosing = true)
        case None =>
          edgesByPair(key) = CandidateCfgEdge(sourceId, targetId, cycleClosing)
        case _ =>
      }
    }

    def visit(node: CfgNode, activePath: Set[Long]): Unit = {
      nodesById.getOrElseUpdate(node.id, node)
      if (!globallyExpanded.contains(node.id)) {
        globallyExpanded += node.id
        node.start.cfgNext.l.foreach { successor =>
          nodesById.getOrElseUpdate(successor.id, successor)
          val closesActivePath = activePath.contains(successor.id)
          record(node.id, successor.id, closesActivePath)
          if (!closesActivePath && !globallyExpanded.contains(successor.id)) {
            visit(successor, activePath + successor.id)
          }
        }
      }
    }

    entryStartPoints.distinctBy(_.id).foreach { start =>
      nodesById.getOrElseUpdate(start.id, start)
      record(method.id, start.id, cycleClosing = false)
      if (!globallyExpanded.contains(start.id)) visit(start, Set(start.id))
    }
    CandidateMethodCfg(
      method.id,
      method.fullName,
      nodesById.toMap,
      edgesByPair.values.toList
    )
  }

  // Path-level arm outcomes. Physical edges own routing; ArmExit records only
  // the outcome and its structural or terminal destination.
  def breakTargetsLoop(breakNode: ControlStructure): Boolean = {
    var current: AstNode = breakNode
    var result = false
    var walking = true
    while (walking) {
      current.start.astParent.headOption match {
        case Some(parent: ControlStructure) =>
          val kind = parent.controlStructureType
          if (Set("FOR", "WHILE", "DO", "DO_WHILE").contains(kind)) {
            result = true
            walking = false
          } else if (kind == "SWITCH") {
            // SWITCH has no structural anchors yet. Its local break remains
            // an ordinary CFG fallthrough to the statement after the switch.
            walking = false
          } else current = parent
        case Some(parent) => current = parent
        case None => walking = false
      }
    }
    result
  }

  def armExits(armRoot: AstNode, armCalls: List[Call]): ujson.Arr = {
    val exits = ujson.Arr()
    val armCallIds = armCalls.map(_.id).toSet
    val armAstNodes = armRoot.ast.l
    val armAstIds = armAstNodes.map(_.id).toSet

    armRoot.ast.collectAll[Return].l.foreach { ret =>
      exits.arr.addOne(ujson.Obj(
        "kind" -> ujson.Str("return"),
        "destinationNodeId" -> ujson.Str(s"r${ret.id}")
      ))
    }

    armCalls.filter(_.methodFullName == "<operator>.throw").foreach { call =>
      exits.arr.addOne(ujson.Obj(
        "kind" -> ujson.Str("throw"),
        "destinationNodeId" -> ujson.Str(s"t${call.id}")
      ))
    }

    armRoot.ast.collectAll[ControlStructure]
      .filter(node =>
        node.controlStructureType == "BREAK" && breakTargetsLoop(node)
      ).l.foreach { breakNode =>
        exits.arr.addOne(ujson.Obj(
          "kind" -> ujson.Str("break"),
          "destinationNodeId" -> ujson.Str(s"k${breakNode.id}")
        ))
      }

    armRoot.ast.collectAll[ControlStructure]
      .filter(_.controlStructureType == "CONTINUE").l.foreach { continueNode =>
        exits.arr.addOne(ujson.Obj(
          "kind" -> ujson.Str("continue"),
          "destinationNodeId" -> ujson.Str(s"n${continueNode.id}")
        ))
      }

    // A mixed arm can have terminal paths and a normal path. Find CFG actual 
    // boundary crossings and store its nearest call node as continuingFrontiers.
    val continuingBoundaries = armAstNodes.collect { case node: CfgNode => node }
      .filter { node =>
        val isReturn = node.label == "RETURN"
        val isThrow = node match {
          case call: Call => call.methodFullName == "<operator>.throw"
          case _          => false
        }
        val isBreak = node match {
          case structure: ControlStructure => structure.controlStructureType == "BREAK"
          case _                           => false
        }
        val isContinue = node match {
          case structure: ControlStructure => structure.controlStructureType == "CONTINUE"
          case _                           => false
        }
        !isReturn && !isThrow && !isBreak && !isContinue &&
          node.start.cfgNext.l.exists(next => !armAstIds.contains(next.id))
      }
    if (continuingBoundaries.nonEmpty || exits.arr.isEmpty) {
      exits.arr.addOne(ujson.Obj(
        "kind" -> ujson.Str("continues")
      ))
    }
    exits
  }

  // Create a branch arm object based on certain branch entry point/ arm root
  def addArm(
    groupId: String, label: String, conditionCode: Option[String],
    armRoot: AstNode, arms: ujson.Arr
  ): List[String] = {
    val armCalls = armRoot.ast.isCall.l
    val armAstNodes = armRoot.ast.l
    val armAstIds = armAstNodes.map(_.id).toSet
    val boundaryCfgNodes = armAstNodes.collect { case node: CfgNode => node }
      .filter(node =>
        node.start.cfgPrev.l.exists(previous => !armAstIds.contains(previous.id))
      )
    val callIds = armCalls.map(call => s"c${call.id}").toSet
    val entryIds = structuralBodyEntryIds(
      armRoot, boundaryCfgNodes, groupId, callIds
    )
    // Package 3: membership is already populated by deriveLexicalOwnership().
    // The former armCalls/armReturns/nestedStructures writes were redundant
    // emitter-side inference and are intentionally removed.
    val exits = armExits(armRoot, armCalls)
    val armObj = ujson.Obj(
      "label" -> label,
      "empty" -> ujson.Bool(armCalls.isEmpty),
      "exits" -> exits
    )
    conditionCode.foreach { cc => armObj("conditionCode") = ujson.Str(cc) }
    arms.arr.addOne(armObj)
    entryIds
  }

  // Add explicit empty arm for `if` with no `else`.
  def addImplicitElse(
    groupId: String, arms: ujson.Arr
  ): Unit = {
    val exit = ujson.Obj(
      "kind" -> ujson.Str("continues")
    )
    arms.arr.addOne(ujson.Obj(
      "label" -> "else",
      "empty" -> ujson.Bool(true),
      "exits" -> ujson.Arr(exit)
    ))
  }

  // Emits ONE group for a whole if / else-if / else chain.
  def emitIfChain(
    head: ControlStructure, methodFullName: String,
    region: BranchRegion,
    structuralOwnership: Map[String, LexicalOwnership],
    methodFallthroughId: Option[String]
  ): Unit = {
    val groupId = s"cs${head.id}"
    val arms = ujson.Arr()
    val stagePlans = mutable.ArrayBuffer[IfStagePlan]()
    val armPlans = mutable.ArrayBuffer[IfArmPlan]()
    val structureAstIds = region.structure.astNodeIds
    val cfgContinuationIds = projectedCfgBoundaryContinuationIds(
      head, structureAstIds
    )
    val structuralContinuationIds = {
      if (cfgContinuationIds.nonEmpty) cfgContinuationIds
      else {
        val following = exceptionalAstStructuralContinuationIds(head)
        if (following.nonEmpty) following else methodFallthroughId.toList
      }
    }

    def recordArm(label: String, heads: List[String], arm: ujson.Obj): Unit = {
      val exits = arm("exits").arr.toList.map(_.obj)
      val terminalIds = exits
        .filter(exit => exit("kind").str != "continues")
        .flatMap(_.get("destinationNodeId").map(_.str))
        .distinct
      armPlans += IfArmPlan(
        label,
        heads.distinct,
        terminalIds,
        exits.exists(_("kind").str == "continues")
      )
    }

    var current = head
    var idx = 0
    var walking = true
    while (walking) {
      val conditionId = current.condition.headOption.map(_.id)
      val armRoots = current.astChildren.l.filterNot(c => conditionId.contains(c.id))
      val label = if (idx == 0) "if" else s"elseif$idx"
      val discoveredStage = region.conditionStages(idx)
      val conditionCalls = current.condition.headOption.toList
        .flatMap(_.start.ast.isCall.l).distinctBy(_.id)
      val conditionCallIds = conditionCalls.map(call => s"c${call.id}").toSet
      val conditionExecutionIds = conditionExecutionCallsOf(current)
        .map(call => s"c${call.id}")
        .filter(conditionCallIds.contains)
      val conditionEntryIds = conditionExecutionIds.headOption.toList
      val conditionExitIds = conditionExecutionIds.lastOption.toList
      stagePlans += IfStagePlan(
        discoveredStage.id,
        discoveredStage.decisionAnchorId,
        conditionCallIds,
        conditionExecutionIds,
        conditionEntryIds,
        conditionExitIds
      )
      armRoots.headOption.foreach { thenRoot =>
        val heads = addArm(
          groupId, label, current.condition.headOption.map(_.code), thenRoot, arms
        )
        recordArm(label, heads, arms.arr.last.obj)
      }
      elseChainNext(current) match {
        case Some(next) =>
          current = next
          idx += 1
        case None =>
          val chainEndsWithElse = armRoots.size > 1
          if (chainEndsWithElse) {
            val heads = addArm(groupId, "else", None, armRoots.last, arms)
            val finalArm = arms.arr.last.obj
            recordArm("else", heads, finalArm)
          } else {
            addImplicitElse(groupId, arms)
            recordArm("else", Nil, arms.arr.last.obj)
          }
          walking = false
      }
    }

    val entryNode = ujson.Obj(
      "id" -> region.structure.entryAnchorId,
      "type" -> "structure",
      "structureGroupId" -> groupId,
      "structureRole" -> "entry",
      "callerMethod" -> methodFullName,
      "line" -> head.lineNumber.getOrElse(-1)
    )
    writeLexicalOwnership(
      entryNode,
      structuralOwnership.getOrElse(region.structure.entryAnchorId, LexicalOwnership(Nil, Nil))
    )
    addNode(region.structure.entryAnchorId, entryNode)

    stagePlans.zipWithIndex.foreach { case (stage, stageIndex) =>
      val stageStructure = if (stageIndex == 0) head else {
        var cursor = head
        var remaining = stageIndex
        while (remaining > 0) {
          cursor = elseChainNext(cursor).get
          remaining -= 1
        }
        cursor
      }
      val decisionNode = ujson.Obj(
        "id" -> stage.decisionNodeId,
        "type" -> "structure",
        "structureGroupId" -> groupId,
        "structureRole" -> "decision",
        "callerMethod" -> methodFullName,
        "code" -> stageStructure.condition.headOption.map(_.code).getOrElse(""),
        "line" -> stageStructure.lineNumber.getOrElse(-1)
      )
      writeLexicalOwnership(
        decisionNode,
        structuralOwnership.getOrElse(stage.decisionNodeId, LexicalOwnership(Nil, Nil))
      )
      addNode(stage.decisionNodeId, decisionNode)
    }

    val hasContinuingArm = armPlans.exists(_.continues)
    val exitNodeId = Option.when(hasContinuingArm)(region.structure.exitAnchorId)
    exitNodeId.foreach { id =>
      val exitNode = ujson.Obj(
        "id" -> id,
        "type" -> "structure",
        "structureGroupId" -> groupId,
        "structureRole" -> "exit",
        "callerMethod" -> methodFullName,
        "line" -> head.lineNumber.getOrElse(-1)
      )
      writeLexicalOwnership(
        exitNode,
        structuralOwnership.getOrElse(id, LexicalOwnership(Nil, Nil))
      )
      addNode(id, exitNode)
    }

    val serializedStages = stagePlans.map { stage =>
      ujson.Obj(
        "id" -> stage.id,
        "nodeIds" -> stringArray(stage.conditionExecutionIds),
        "decisionNodeId" -> stage.decisionNodeId
      )
    }.toList
    arms.arr.zipWithIndex.foreach { case (armValue, armIndex) =>
      val selectedStageCount = math.min(armIndex + 1, stagePlans.size)
      val isElse = armValue.obj("label").str == "else"
      val selections = stagePlans.take(selectedStageCount).zipWithIndex.map {
        case (stage, stageIndex) => ujson.Obj(
          "stageId" -> stage.id,
          "nodeIds" -> stringArray(stage.conditionExecutionIds),
          "outcome" -> ujson.Bool(!isElse && stageIndex == selectedStageCount - 1)
        )
      }
      armValue.obj("conditionStages") = ujson.Arr(selections.toList*)
    }

    val group = ujson.Obj(
      "id" -> groupId, "kind" -> "IF",
      "method" -> methodFullName,
      "line" -> head.lineNumber.getOrElse(-1),
      "entryNodeId" -> region.structure.entryAnchorId,
      "conditionStages" -> ujson.Arr(serializedStages*),
      "arms" -> arms
    )
    val enclosingRequirements = structuralOwnership.getOrElse(
      region.structure.entryAnchorId, LexicalOwnership(Nil, Nil)
    ).branchArms
    if (enclosingRequirements.nonEmpty) {
      group("enclosingRequirements") = serializedBranchRequirements(
        enclosingRequirements
      )
    }
    exitNodeId.foreach(id => group("exitNodeId") = ujson.Str(id))
    branchGroups += group
    pendingIfAnchorPlans += IfAnchorPlan(
      groupId,
      region.structure.astNodeIds.size,
      region.structure.entryAnchorId,
      exitNodeId,
      stagePlans.toList,
      armPlans.toList,
      structuralContinuationIds.distinct,
      structuralOwnership.getOrElse(
        region.structure.entryAnchorId, LexicalOwnership(Nil, Nil)
      ).branchArms,
      structuralOwnership.getOrElse(
        region.structure.entryAnchorId, LexicalOwnership(Nil, Nil)
      ).loopIds
    )
  }

  // Emits ONE group for a try's mutually exclusive outcomes. The try body
  // itself is the common spine before the fork, and finally is common flow
  // after it, so neither is an arm. 
  def emitTryGroup(
    cs: ControlStructure,
    method: Method,
    region: BranchRegion,
    structuralOwnership: Map[String, LexicalOwnership],
    methodFallthroughId: Option[String]
  ): Unit = {
    val groupId = s"cs${cs.id}"
    val armRoots = cs.astChildren.l
    val arms = ujson.Arr()
    val ordinaryVariableNames = (method.parameter.name.l ++ method.local.name.l).toSet
    var catchIdx = 0
    val catchPlans = mutable.ArrayBuffer[TryArmPlan]()

    def projectedIds(root: AstNode): Set[String] =
      root.start.ast.l.flatMap {
        case call: Call =>
          val callId = s"c${call.id}"
          if (call.methodFullName == "<operator>.throw") List(callId, s"t${call.id}")
          else List(callId)
        case ret: Return => List(s"r${ret.id}")
        case structure: ControlStructure
            if structure.controlStructureType == "BREAK" &&
              breakTargetsLoop(structure) => List(s"k${structure.id}")
        case structure: ControlStructure
            if structure.controlStructureType == "CONTINUE" => List(s"n${structure.id}")
        case _ => Nil
      }.toSet

    val tryBodyRoot = armRoots.find {
      case structure: ControlStructure =>
        !Set("CATCH", "FINALLY").contains(structure.controlStructureType)
      case _ => true
    }
    val finallyRoot = armRoots.collectFirst {
      case structure: ControlStructure
          if structure.controlStructureType == "FINALLY" => structure
    }
    armRoots.foreach { armRoot =>
      val structureType = armRoot match {
        case c: ControlStructure => c.controlStructureType
        case _                   => ""
      }
      if (structureType == "CATCH") {
        catchIdx += 1
        val label = s"catch$catchIdx"
        val heads = addArm(groupId, label, None, armRoot, arms)
        val astIds = armRoot.start.ast.l.map(_.id).toSet
        catchPlans += TryArmPlan(label, astIds, projectedIds(armRoot), heads)
        val exceptionType = armRoot.ast.isIdentifier
          .filterNot(i => ordinaryVariableNames.contains(i.name))
          .map(_.typeFullName)
          .find(t => t.nonEmpty && t != "<empty>")
        exceptionType.foreach { t =>
          arms.arr.last.obj("exceptionType") = ujson.Str(t)
        }
      }
    }
    arms.arr.addOne(ujson.Obj(
      "label" -> "noCatch",
      "empty" -> ujson.Bool(true),
      "exits" -> ujson.Arr(ujson.Obj(
        "kind" -> ujson.Str("continues")
      ))
    ))
    val entryNode = ujson.Obj(
      "id" -> region.structure.entryAnchorId,
      "type" -> "structure",
      "structureGroupId" -> groupId,
      "structureRole" -> "entry",
      "callerMethod" -> method.fullName,
      "line" -> cs.lineNumber.getOrElse(-1)
    )
    writeLexicalOwnership(
      entryNode,
      structuralOwnership.getOrElse(
        region.structure.entryAnchorId, LexicalOwnership(Nil, Nil)
      )
    )
    addNode(region.structure.entryAnchorId, entryNode)

    val decisionNodeId = s"$groupId:decision"
    val decisionNode = ujson.Obj(
      "id" -> decisionNodeId,
      "type" -> "structure",
      "structureGroupId" -> groupId,
      "structureRole" -> "decision",
      "callerMethod" -> method.fullName,
      "code" -> "catch dispatch",
      "line" -> cs.lineNumber.getOrElse(-1)
    )
    writeLexicalOwnership(
      decisionNode,
      structuralOwnership.getOrElse(
        region.structure.entryAnchorId, LexicalOwnership(Nil, Nil)
      )
    )
    addNode(decisionNodeId, decisionNode)

    val exitNode = ujson.Obj(
      "id" -> region.structure.exitAnchorId,
      "type" -> "structure",
      "structureGroupId" -> groupId,
      "structureRole" -> "exit",
      "callerMethod" -> method.fullName,
      "line" -> cs.lineNumber.getOrElse(-1)
    )
    writeLexicalOwnership(
      exitNode,
      structuralOwnership.getOrElse(
        region.structure.exitAnchorId, LexicalOwnership(Nil, Nil)
      )
    )
    addNode(region.structure.exitAnchorId, exitNode)

    val group = ujson.Obj(
      "id" -> groupId, "kind" -> "TRY",
      "method" -> method.fullName,
      "line" -> cs.lineNumber.getOrElse(-1),
      "entryNodeId" -> region.structure.entryAnchorId,
      "exitNodeId" -> region.structure.exitAnchorId,
      "conditionStages" -> ujson.Arr(),
      "arms" -> arms
    )
    val enclosingRequirements = structuralOwnership.getOrElse(
      region.structure.entryAnchorId, LexicalOwnership(Nil, Nil)
    ).branchArms
    if (enclosingRequirements.nonEmpty) {
      group("enclosingRequirements") = serializedBranchRequirements(
        enclosingRequirements
      )
    }
    branchGroups += group

    val cfgContinuationIds = projectedCfgBoundaryContinuationIds(
      cs, region.structure.astNodeIds
    )
    val continuationIds = {
      if (cfgContinuationIds.nonEmpty) cfgContinuationIds
      else {
        val following = exceptionalAstStructuralContinuationIds(cs)
        if (following.nonEmpty) following else methodFallthroughId.toList
      }
    }
    val tryBodyAstIds = tryBodyRoot.toList.flatMap(_.start.ast.l.map(_.id)).toSet
    val tryBodyCfgNodes = tryBodyRoot.toList.flatMap(_.start.ast.l.collect {
      case node: CfgNode => node
    })
    val tryBodyEntryCfgNodes = tryBodyCfgNodes.filter { node =>
      node.start.cfgPrev.l.exists(previous => !tryBodyAstIds.contains(previous.id))
    }
    val tryBodyEntryIds = nearestProjectedIds(tryBodyEntryCfgNodes)
    val finallyAstIds = finallyRoot.toList.flatMap(_.start.ast.l.map(_.id)).toSet
    val enclosure = structuralOwnership.getOrElse(
      region.structure.entryAnchorId, LexicalOwnership(Nil, Nil)
    )
    pendingTryAnchorPlans += TryAnchorPlan(
      groupId,
      region.structure.astNodeIds.size,
      region.structure.entryAnchorId,
      decisionNodeId,
      region.structure.exitAnchorId,
      tryBodyAstIds,
      tryBodyRoot.map(projectedIds).getOrElse(Set.empty),
      tryBodyEntryIds,
      catchPlans.toList,
      finallyAstIds,
      finallyRoot.map(projectedIds).getOrElse(Set.empty),
      continuationIds,
      enclosure.branchArms,
      enclosure.loopIds
    )
  }

  // Loops are repetition metadata, not mutually-exclusive branch arms.
  // Keep their source guard even when its implementation is stripped as
  // java.util/operator noise, and tag calls in the lexical BODY only.
  // Java loop bodies are represented by BLOCK children even when the
  // source omitted braces; selecting BLOCK also excludes a traditional
  // for-loop's one-time initializer and per-iteration update expressions.
  def emitLoopGroup(
    cs: ControlStructure,
    region: LoopRegion,
    structuralOwnership: Map[String, LexicalOwnership],
    methodFullName: String,
    candidateNextCallsById: Map[Long, List[Call]],
    methodFallthroughId: Option[String]
  ): LoopAnchorPlan = {
    val groupId = s"loop${cs.id}"
    val rawCondition = cs.condition.headOption.map(_.code).getOrElse("").trim
    // FOR has two BLOCK children in the Java CPG: order 1 is initializer,
    // the LAST block is the repeating body. WHILE/DO use the same final
    // block convention. Taking every block incorrectly marks `int i = 0`
    // as repeated.
    val blocks = cs.astChildren.l.filter(_.label == "BLOCK")
    val bodyRoots = blocks.lastOption.toList
    val bodyAstNodes = bodyRoots.flatMap(_.ast.l)
    val bodyAstIds = bodyAstNodes.map(_.id).toSet
    val bodyCfgNodes = bodyAstNodes.collect { case node: CfgNode => node }
    val bodyCalls = bodyRoots.flatMap(_.ast.isCall.l)
      .groupBy(_.id).values.map(_.head).toList
    // Package 3: body/guard/update membership is assigned once by
    // deriveLexicalOwnership(); loop emission only describes anchor routes.

    // This product intentionally models one complete lexical iteration.
    // Remove zero-iteration and next-iteration alternatives; reconnect only
    // completed iteration frontiers to the lexical statement after the loop.
    //
    // WHILE/DO with a call-free guard project directly back to the first body
    // call. FOR projects update -> condition. Enhanced-for projects the body
    // tail -> Iterator.hasNext(). The classification comes from the loop AST
    // regions rather than graph visitation order, which merely prevents
    // infinite traversal and does not identify a back edge.
    val sourceHeader = cs.code.trim
    val isSourceFor = cs.controlStructureType == "FOR" ||
      sourceHeader.startsWith("for (") || rawCondition.startsWith("for (")
    val forHeader =
      if (sourceHeader.startsWith("for (")) sourceHeader else rawCondition
    val displayCondition =
      if (isSourceFor) forHeader.takeWhile(_ != '{').trim
      else rawCondition
    val isSourceForEach = isSourceFor && displayCondition.contains(":")
    val bodyEntryNodes = bodyCfgNodes.filter { node =>
      node.start.cfgPrev.l.exists(previous => !bodyAstIds.contains(previous.id))
    }
    val bodyEntryCallIds = nearestProjectedIds(bodyEntryNodes)
      .filter(_.startsWith("c"))
      .flatMap(id => id.drop(1).toLongOption)
      .toSet
    val rawConditionCalls = cs.condition.headOption.toList
      .flatMap(_.start.ast.isCall.l)
      .distinctBy(_.id)
    // Java enhanced-for is lowered into a WHILE whose condition AST can own
    // the complete source loop. Consequently rawConditionCalls may contain
    // iterator setup, next()/assignment, and lexical body operations as well
    // as hasNext(). A guard frontier is specifically an outside-body call
    // whose next projected call enters the lexical body. Keeping this set
    // narrow prevents the preheader -> guard edge from being mistaken for a
    // next-iteration edge.
    val conditionCalls =
      if (isSourceForEach) {
        rawConditionCalls.filter { call =>
          !bodyAstIds.contains(call.id) &&
          candidateNextCallsById.getOrElse(call.id, Nil)
            .exists(next => bodyEntryCallIds.contains(next.id))
        }
      } else rawConditionCalls
    val conditionCallIds = conditionCalls.map(_.id).toSet
    val updateCalls = cs.ast.isCall.l
      .filter(call => region.updateNodeIds.contains(call.id))
      .distinctBy(_.id)
    val isDoLoop = Set("DO", "DO_WHILE").contains(cs.controlStructureType)
    val cfgContinuationIds = projectedCfgBoundaryContinuationIds(
      cs, region.structure.astNodeIds
    )
    val structuralContinuationIds = {
      if (cfgContinuationIds.nonEmpty) cfgContinuationIds
      else {
        val following = exceptionalAstStructuralContinuationIds(cs)
        if (following.nonEmpty) following else methodFallthroughId.toList
      }
    }
    val initialTargetIds =
      if (isDoLoop) bodyEntryCallIds
      else if (conditionCalls.nonEmpty) conditionCallIds
      else bodyEntryCallIds
    val bodyProjectedIds = bodyCalls.map(call => s"c${call.id}").toSet
    val structuralBodyEntries = bodyRoots.headOption.toList.flatMap { bodyRoot =>
      structuralBodyEntryIds(
        bodyRoot, bodyEntryNodes, groupId, bodyProjectedIds
      )
    }
    val initialProjectedIds =
      if (isDoLoop && structuralBodyEntries.nonEmpty) structuralBodyEntries.toSet
      else if (conditionCalls.nonEmpty) conditionCallIds.map(id => s"c$id")
      else if (structuralBodyEntries.nonEmpty) structuralBodyEntries.toSet
      else initialTargetIds.map(id => s"c$id")
    // Enhanced-for is lowered to WHILE by the Java frontend, whose
    // "condition" code is the whole source loop. Recover its source-facing
    // kind and keep only the header for the UI tooltip.
    val sourceKind =
      if (isSourceForEach) "FOR_EACH"
      else if (isSourceFor) "FOR"
      else cs.controlStructureType
    val loopObj = ujson.Obj(
      "id" -> groupId,
      "kind" -> sourceKind,
      "method" -> methodFullName,
      "line" -> cs.lineNumber.getOrElse(-1),
      "entryNodeId" -> region.structure.entryAnchorId,
      "exitNodeId" -> region.structure.exitAnchorId
    )
    Option(displayCondition).filter(_.nonEmpty).foreach { code =>
      loopObj("conditionCode") = ujson.Str(code)
    }
    loopGroups += loopObj

    val entryNode = ujson.Obj(
      "id" -> region.structure.entryAnchorId,
      "type" -> "structure",
      "structureGroupId" -> groupId,
      "structureRole" -> "entry",
      "callerMethod" -> methodFullName,
      "line" -> cs.lineNumber.getOrElse(-1)
    )
    writeLexicalOwnership(
      entryNode,
      structuralOwnership.getOrElse(
        region.structure.entryAnchorId, LexicalOwnership(Nil, Nil)
      )
    )
    addNode(region.structure.entryAnchorId, entryNode)

    val exitNode = ujson.Obj(
      "id" -> region.structure.exitAnchorId,
      "type" -> "structure",
      "structureGroupId" -> groupId,
      "structureRole" -> "exit",
      "callerMethod" -> methodFullName,
      "line" -> cs.lineNumber.getOrElse(-1)
    )
    writeLexicalOwnership(
      exitNode,
      structuralOwnership.getOrElse(
        region.structure.exitAnchorId, LexicalOwnership(Nil, Nil)
      )
    )
    addNode(region.structure.exitAnchorId, exitNode)

    LoopAnchorPlan(
      groupId,
      sourceKind,
      bodyCalls.map(call => s"c${call.id}").toSet,
      conditionCallIds.map(id => s"c$id"),
      updateCalls.map(call => s"c${call.id}").toSet,
      initialProjectedIds,
      structuralContinuationIds.toSet,
      region.structure.entryAnchorId,
      region.structure.exitAnchorId,
      region.structure.astNodeIds.size
    )
  }

  cpg.method.isExternal(false).whereNot(_.isAbstract).l.foreach { method =>
    val entryId = s"m${method.id}"
    val entryNode = ujson.Obj(
      "id" -> entryId, "type" -> "entry",
      "calleeFullName" -> method.fullName,
      "sourceFile" -> methodSourceFile(method),
      "line" -> method.lineNumber.getOrElse(-1)
    )
    if (method.name == "<init>" && method.code == "<empty>") {
      entryNode("implicitConstructor") = ujson.Bool(true)
    }
    addNode(entryId, entryNode)

    val methodCalls = method.call.l
    val explicitReturns = method.ast.collectAll[Return].l
    val explicitBreaks = method.controlStructure
      .filter(node =>
        node.controlStructureType == "BREAK" && breakTargetsLoop(node)
      ).l
    val explicitContinues = method.controlStructure
      .filter(_.controlStructureType == "CONTINUE").l
    val methodReturn = Option(method.methodReturn)
    val fallthroughExitId = methodReturn.map(ret => s"f${ret.id}")

    explicitReturns.foreach { ret =>
      val exitId = s"r${ret.id}"
      addNode(exitId, ujson.Obj(
        "id" -> exitId, "type" -> "exit", "exitKind" -> "return",
        "callerMethod" -> method.fullName,
        "sourceFile" -> methodSourceFile(method),
        "code" -> ret.code, "line" -> ret.lineNumber.getOrElse(-1)
      ))
    }
    explicitBreaks.foreach { breakNode =>
      val exitId = s"k${breakNode.id}"
      addNode(exitId, ujson.Obj(
        "id" -> exitId, "type" -> "transfer", "transferKind" -> "break",
        "callerMethod" -> method.fullName,
        "sourceFile" -> methodSourceFile(method),
        "code" -> breakNode.code, "line" -> breakNode.lineNumber.getOrElse(-1)
      ))
    }
    explicitContinues.foreach { continueNode =>
      val exitId = s"n${continueNode.id}"
      addNode(exitId, ujson.Obj(
        "id" -> exitId, "type" -> "transfer", "transferKind" -> "continue",
        "callerMethod" -> method.fullName,
        "sourceFile" -> methodSourceFile(method),
        "code" -> continueNode.code, "line" -> continueNode.lineNumber.getOrElse(-1)
      ))
    }
    // Joern can expose METHOD_RETURN as a bookkeeping successor of METHOD
    // alongside the real body entry. Treating that synthetic shortcut as an
    // executable route creates entry -> fallthrough even when every source
    // path first executes the body (and some paths explicitly return).
    // Preserve METHOD_RETURN only for a genuinely empty method frontier.
    val rawEntryStartPoints = method.start.cfgNext.l
    val bodyEntryStartPoints = rawEntryStartPoints.filterNot { node =>
      methodReturn.exists(_.id == node.id)
    }
    val entryStartPoints =
      if (bodyEntryStartPoints.nonEmpty) bodyEntryStartPoints
      else rawEntryStartPoints
    val candidateCfg = discoverCandidateCfg(method, entryStartPoints)
    discoveredCandidateCfgs += candidateCfg
    val candidateCallsById = candidateCfg.nodesById.collect {
      case (id, call: Call) => id -> call
    }
    val candidateOutgoing = candidateCfg.edges.groupMap(_.sourceNodeId)(_.targetNodeId)
    val explicitBreakIds = explicitBreaks.map(_.id).toSet
    val explicitContinueIds = explicitContinues.map(_.id).toSet
    def candidateProjectedIds(startNodeIds: List[Long]): List[String] = {
      val pending = mutable.Queue.from(startNodeIds)
      val seen = mutable.Set[Long]()
      val found = mutable.LinkedHashSet[String]()
      while (pending.nonEmpty) {
        val nodeId = pending.dequeue()
        if (!seen.contains(nodeId)) {
          seen += nodeId
          candidateCfg.nodesById.get(nodeId) match {
            case Some(call: Call) => found += s"c${call.id}"
            case Some(_: Return) => found += s"r$nodeId"
            case Some(structure: ControlStructure)
                if explicitBreakIds.contains(structure.id) =>
              found += s"k${structure.id}"
            case Some(structure: ControlStructure)
                if explicitContinueIds.contains(structure.id) =>
              found += s"n${structure.id}"
            case Some(node) if methodReturn.exists(_.id == node.id) =>
              found += s"f${node.id}"
            case Some(_) =>
              candidateOutgoing.getOrElse(nodeId, Nil).foreach(pending.enqueue(_))
            case None =>
          }
        }
      }
      if (found.isEmpty && startNodeIds.isEmpty) fallthroughExitId.toList
      else found.toList
    }
    val nestedCallIdsByCallId = methodCalls.map { call =>
      call.id -> call.start.ast.isCall.id.l.filterNot(_ == call.id).toSet
    }.toMap
    def canonicalProjectedSuccessors(call: Call): List[String] =
      candidateProjectedIds(candidateOutgoing.getOrElse(call.id, Nil)).filterNot {
        targetId =>
          targetId.stripPrefix("c").toLongOption.exists { targetCallId =>
            nestedCallIdsByCallId.getOrElse(call.id, Set.empty).contains(targetCallId)
          }
      }
    // Package 4 ordering switch: ordinary projected sequence comes from the
    // candidate CFG. LinkedHashSet traversal preserves Joern's successor
    // order without inventing a total order between alternative branches.
    val candidateNextCallsById: Map[Long, List[Call]] = methodCalls.map { call =>
      call.id -> canonicalProjectedSuccessors(call)
        .flatMap(_.stripPrefix("c").toLongOption)
        .flatMap(candidateCallsById.get)
    }.toMap

    val deadEndCallIds: Set[Long] = methodCalls.flatMap { c =>
      val nextCalls = candidateNextCallsById(c.id)
      Option.when(
        nextCalls.nonEmpty &&
        nextCalls.forall(_.methodFullName == "<operator>.throw")
      )(c.id)
    }.toSet

    // Identify all control structure info before node emission. Package 3
    // derives every lexical membership in one pass before topology changes.
    val controlStructures = method.controlStructure.l
    val ifs = controlStructures.filter(_.controlStructureType == "IF")
    val chainedIfIds = ifs.flatMap(cs => elseChainNext(cs).map(_.id)).toSet
    val ifHeads = ifs.filterNot(cs => chainedIfIds.contains(cs.id))
    val ifHeadIdByChainMemberId = mutable.LinkedHashMap[Long, Long]()
    ifHeads.foreach { head =>
      var current = head
      var walking = true
      while (walking) {
        ifHeadIdByChainMemberId(current.id) = head.id
        elseChainNext(current) match {
          case Some(next) => current = next
          case None => walking = false
        }
      }
    }
    val groupIdByControlStructureId = controlStructures.flatMap { structure =>
      structure.controlStructureType match {
        case "IF" => ifHeadIdByChainMemberId.get(structure.id)
          .map(headId => structure.id -> s"cs$headId")
        case "TRY" => Some(structure.id -> s"cs${structure.id}")
        case kind if Set("FOR", "WHILE", "DO", "DO_WHILE").contains(kind) =>
          Some(structure.id -> s"loop${structure.id}")
        case _ => None
      }
    }.toMap

    def nearestTransferTarget(
      transfer: ControlStructure,
      continueOnly: Boolean
    ): Option[String] = {
      var current: AstNode = transfer
      var result: Option[String] = None
      var walking = true
      while (walking) {
        current.start.astParent.headOption match {
          case Some(parent: ControlStructure) =>
            val kind = parent.controlStructureType
            if (Set("FOR", "WHILE", "DO", "DO_WHILE").contains(kind)) {
              result = groupIdByControlStructureId.get(parent.id)
              walking = false
            } else if (!continueOnly && kind == "SWITCH") {
              // A switch-local break must not fall through to an outer loop.
              // SWITCH is not an anchored structure; its break stays local.
              walking = false
            } else current = parent
          case Some(parent) => current = parent
          case None => walking = false
        }
      }
      result
    }
    val transferTargetByNodeId = (
      explicitBreaks.flatMap(node => nearestTransferTarget(node, false).map(node.id -> _)) ++
      explicitContinues.flatMap(node => nearestTransferTarget(node, true).map(node.id -> _))
    ).toMap

    // Collect lexical regions and physical CFG boundaries before normalization.
    val tryStructures = controlStructures.filter(_.controlStructureType == "TRY")
    val methodBranchRegions =
      ifHeads.map(head => discoverBranchRegion(head, groupIdByControlStructureId)) ++
      tryStructures.map(structure => discoverTryRegion(structure, groupIdByControlStructureId))
    val loopStructures = controlStructures.filter(cs =>
      Set("FOR", "WHILE", "DO", "DO_WHILE").contains(cs.controlStructureType)
    )
    val methodLoopRegions = loopStructures.map(
      structure => discoverLoopRegion(structure, groupIdByControlStructureId)
    )
    discoveredBranchRegions ++= methodBranchRegions
    discoveredLoopRegions ++= methodLoopRegions
    val (armTags, loopTags, structuralOwnership) =
      deriveLexicalOwnership(methodBranchRegions, methodLoopRegions)
    validateLexicalOwnership(
      methodBranchRegions,
      methodLoopRegions,
      armTags,
      loopTags,
      structuralOwnership
    )

    // Each group records its owning method.
    val branchRegionByRootId = methodBranchRegions.map(
      region => region.structure.rootAstNodeId -> region
    ).toMap
    ifHeads.foreach { head =>
      emitIfChain(
        head, method.fullName, branchRegionByRootId(head.id),
        structuralOwnership,
        fallthroughExitId
      )
    }
    val loopRegionByRootId = methodLoopRegions.map(
      region => region.structure.rootAstNodeId -> region
    ).toMap
    val staticLoopRoutes = loopStructures.map { cs =>
      emitLoopGroup(
        cs,
        loopRegionByRootId(cs.id),
        structuralOwnership,
        method.fullName,
        candidateNextCallsById,
        fallthroughExitId
      )
    }
    pendingLoopAnchorPlans ++= staticLoopRoutes
    tryStructures.foreach { cs =>
      emitTryGroup(
        cs,
        method,
        branchRegionByRootId(cs.id),
        structuralOwnership,
        fallthroughExitId
      )
    }
    // Apply the same authoritative ownership to terminal transfer nodes.
    explicitReturns.foreach { ret =>
      writeLexicalOwnership(
        nodes(s"r${ret.id}"), lexicalOwnershipAt(ret.id, armTags, loopTags)
      )
    }
    explicitBreaks.foreach { breakNode =>
      val node = nodes(s"k${breakNode.id}")
      writeLexicalOwnership(node, lexicalOwnershipAt(breakNode.id, armTags, loopTags))
      transferTargetByNodeId.get(breakNode.id).foreach { targetGroupId =>
        node("targetStructureGroupId") = ujson.Str(targetGroupId)
        pendingStructuralTransfers += StructuralTransferPlan(
          s"k${breakNode.id}", targetGroupId
        )
      }
    }
    explicitContinues.foreach { continueNode =>
      val node = nodes(s"n${continueNode.id}")
      writeLexicalOwnership(
        node, lexicalOwnershipAt(continueNode.id, armTags, loopTags)
      )
      transferTargetByNodeId.get(continueNode.id).foreach { targetGroupId =>
        node("targetStructureGroupId") = ujson.Str(targetGroupId)
        pendingStructuralTransfers += StructuralTransferPlan(
          s"n${continueNode.id}", targetGroupId
        )
      }
    }

    val entryTargets = candidateProjectedIds(entryStartPoints.map(_.id))
    entryTargets.foreach { targetId =>
      edges += ujson.Obj("from" -> entryId, "to" -> targetId, "type" -> "sequence")
    }

    methodCalls.foreach { call =>
      val callId = s"c${call.id}"

      // intra-method sequence: next call(s) after this one. A throw operator
      // is terminal even if Joern's syntactic CFG exposes later statements;
      // it owns only the structural throw-exit edge emitted below.
      if (call.methodFullName != "<operator>.throw") {
        canonicalProjectedSuccessors(call).foreach {
          targetId =>
            edges += ujson.Obj(
              "from" -> callId, "to" -> targetId, "type" -> "sequence"
            )
        }
      }
      if (call.methodFullName == "<operator>.throw") {
        val throwExitId = s"t${call.id}"
        val throwExit = ujson.Obj(
          "id" -> throwExitId, "type" -> "exit", "exitKind" -> "throw",
          "callerMethod" -> method.fullName,
          "sourceFile" -> methodSourceFile(method),
          "code" -> call.code, "line" -> call.lineNumber.getOrElse(-1)
        )
        writeLexicalOwnership(
          throwExit, lexicalOwnershipAt(call.id, armTags, loopTags)
        )
        addNode(throwExitId, throwExit)
        edges += ujson.Obj("from" -> callId, "to" -> throwExitId, "type" -> "sequence")
      }

      val callNode = ujson.Obj(
        "id" -> callId, "type" -> "call",
        "callerMethod" -> method.fullName, "calleeFullName" -> call.methodFullName,
        "sourceFile" -> methodSourceFile(method),
        "code" -> call.code, "line" -> call.lineNumber.getOrElse(-1)
      )
      writeLexicalOwnership(
        callNode, lexicalOwnershipAt(call.id, armTags, loopTags)
      )
      if (deadEndCallIds.contains(call.id)) {
        callNode("deadEnd") = ujson.Bool(true)
      }
      addNode(callId, callNode)
      semanticFeatures(callId) = semanticFeature(call)

      // interprocedural traversal
      val resolvedCallees = call.callee.whereNot(_.isAbstract).l
      val traversableCallees = resolvedCallees.filter(
        m => m.isExternal || m.block.astChildren.nonEmpty
      )
      if (resolvedCallees.isEmpty) {
        addNode(callId + "_unresolved", ujson.Obj(
          "id" -> (callId + "_unresolved"), "type" -> "leaf", "reason" -> "unresolved"
        ))
        edges += ujson.Obj("from" -> callId, "to" -> (callId + "_unresolved"), "type" -> "invoke")
      } else {
        traversableCallees.foreach { callee =>
          val calleeEntryId = s"m${callee.id}"
          if (callee.isExternal) {
            addNode(calleeEntryId, ujson.Obj(
              "id" -> calleeEntryId, "type" -> "leaf", "calleeFullName" -> callee.fullName
            ))
            edges += ujson.Obj("from" -> callId, "to" -> calleeEntryId, "type" -> "invoke")
          } else {
            edges += ujson.Obj("from" -> callId, "to" -> calleeEntryId, "type" -> "invoke")
          }
        }
      }

      // Bridge lambda implementation via METHOD_REF targets.
      referencedInternalMethods(call).foreach { implementation =>
        edges += ujson.Obj(
          "from" -> callId,
          "to" -> s"m${implementation.id}",
          "type" -> "invoke"
        )
      }
    }

    // Structure normalization may expose fallthrough later, so defer the
    // decision to materialize METHOD_RETURN until all anchors are composed.
    fallthroughExitId.foreach { exitId =>
      pendingFallthroughPlans += FallthroughPlan(
        exitId,
        method.fullName,
        methodSourceFile(method),
        method.lineNumberEnd.getOrElse(method.lineNumber.getOrElse(-1))
      )
    }
  }

  // Package 5: replace the physical IF fork with explicit structure
  // entry, condition-stage decisions and (when reachable) structure exit.
  // Normalize outer groups first so a subsequently-normalized nested group
  // can redirect the parent's newly-created arm edge through its own entry.
  def addIfSequence(
    source: String,
    target: String,
    requirement: Option[(String, String)] = None
  ): Unit = {
    requirement.foreach(value =>
      recordStructuralRouteRequirements(source, target, List(value))
    )
    if (source != target && !edges.exists { edge =>
      edge.obj.get("type").exists(_.str == "sequence") &&
      edge.obj("from").str == source && edge.obj("to").str == target
    }) {
      edges += ujson.Obj("from" -> source, "to" -> target, "type" -> "sequence")
    }
  }

  pendingIfAnchorPlans.sortBy(plan => -plan.astSize).foreach { plan =>
    val decisionIds = plan.stages.map(_.decisionNodeId).toSet
    val firstStage = plan.stages.head
    val firstCalls = firstStage.conditionCallIds
    val preferredFirstEntries = firstStage.conditionEntryIds.toSet
    val firstEntryTargets = if (preferredFirstEntries.nonEmpty) {
      preferredFirstEntries
    } else firstCalls

    val externalPredecessors = mutable.LinkedHashSet[String]()
    edges.foreach { edge =>
      if (edge.obj.get("type").exists(_.str == "sequence")) {
        val source = edge.obj("from").str
        val target = edge.obj("to").str
        val entersExistingEntry = target == plan.entryNodeId && source != plan.entryNodeId
        val entersFirstCalls = firstEntryTargets.contains(target) && !firstCalls.contains(source)
        val entersFirstDecision = target == firstStage.decisionNodeId &&
          !firstStage.conditionExitIds.contains(source)
        if (entersExistingEntry || entersFirstCalls || entersFirstDecision) {
          externalPredecessors += source
        }
      }
    }

    edges.filterInPlace { edge =>
      if (!edge.obj.get("type").exists(_.str == "sequence")) true
      else {
        val source = edge.obj("from").str
        val target = edge.obj("to").str
        val isAnchorEdge = source == plan.entryNodeId || target == plan.entryNodeId ||
          plan.exitNodeId.contains(source) || plan.exitNodeId.contains(target)
        val isDecisionEdge = decisionIds.contains(source) || decisionIds.contains(target)
        val isInternalConditionEdge = plan.stages.exists { stage =>
          stage.conditionCallIds.contains(source) &&
            stage.conditionCallIds.contains(target)
        }
        val leavesConditionStage = plan.stages.exists { stage =>
          stage.conditionExitIds.contains(source) &&
            !stage.conditionCallIds.contains(target)
        }
        val entersFirstStage = firstEntryTargets.contains(target) && !firstCalls.contains(source)
        !(isAnchorEdge || isDecisionEdge || isInternalConditionEdge ||
          leavesConditionStage || entersFirstStage)
      }
    }

    externalPredecessors.foreach(source => addIfSequence(source, plan.entryNodeId))
    if (firstEntryTargets.nonEmpty) {
      firstEntryTargets.foreach(target => addIfSequence(plan.entryNodeId, target))
    } else {
      addIfSequence(plan.entryNodeId, firstStage.decisionNodeId)
    }

    plan.stages.zipWithIndex.foreach { case (stage, index) =>
      stage.conditionExecutionIds.sliding(2).foreach {
        case List(source, target) => addIfSequence(source, target)
        case _ =>
      }
      if (stage.conditionExitIds.nonEmpty) {
        stage.conditionExitIds.foreach(source => addIfSequence(source, stage.decisionNodeId))
      } else if (stage.conditionEntryIds.isEmpty) {
        if (index == 0) addIfSequence(plan.entryNodeId, stage.decisionNodeId)
      }

      val selectedArm = plan.arms(index)
      val selectedTargets = (
        selectedArm.entryIds ++
          (if (selectedArm.entryIds.isEmpty) selectedArm.terminalIds else Nil) ++
          (if (selectedArm.continues && selectedArm.entryIds.isEmpty)
             plan.exitNodeId.toList else Nil)
      ).distinct
      selectedTargets.foreach { target =>
        addIfSequence(stage.decisionNodeId, target, Some(plan.groupId -> selectedArm.label))
      }

      if (index + 1 < plan.stages.size) {
        val next = plan.stages(index + 1)
        val nextTargets = if (next.conditionEntryIds.nonEmpty) {
          next.conditionEntryIds
        } else List(next.decisionNodeId)
        nextTargets.foreach(target => addIfSequence(stage.decisionNodeId, target))
      } else {
        val finalArm = plan.arms.last
        val finalTargets = (
          finalArm.entryIds ++
            (if (finalArm.entryIds.isEmpty) finalArm.terminalIds else Nil) ++
            (if (finalArm.continues && finalArm.entryIds.isEmpty)
               plan.exitNodeId.toList else Nil)
        ).distinct
        finalTargets.foreach { target =>
          addIfSequence(stage.decisionNodeId, target, Some(plan.groupId -> finalArm.label))
        }
      }
    }

    plan.exitNodeId.foreach { exitId =>
      plan.continuationIds.foreach(target => addIfSequence(exitId, target))
    }
  }

  // Package 8: one authoritative loop normalization pass. Candidate CFG
  // discovery retains cycle-closing topology; projected role sets decide
  // which physical edge begins another iteration for each loop kind.
  def nodeOwnsLoop(nodeId: String, groupId: String): Boolean =
    nodes.get(nodeId).exists { node =>
      node.obj.get("loopIds").exists(_.arr.exists(_.str == groupId))
    }

  def nodeLoopIds(nodeId: String): List[String] =
    nodes.get(nodeId).toList.flatMap { node =>
      node.obj.get("loopIds").toList.flatMap(_.arr.toList.map(_.str))
    }

  val candidateCycleClosingKeys = discoveredCandidateCfgs.flatMap(
    _.edges.filter(_.cycleClosing).map(edge =>
      (s"c${edge.sourceNodeId}", s"c${edge.targetNodeId}")
    )
  ).toSet

  def cycleOwner(source: String, target: String): Option[String] = {
    val targetLoops = nodeLoopIds(target).toSet
    nodeLoopIds(source).filter(targetLoops.contains).lastOption
  }

  // Normalize child loops before their parents. Once a child owns explicit
  // entry/exit boundaries, the parent treats it as an opaque body segment.
  pendingLoopAnchorPlans.sortBy(_.astSize).foreach { plan =>
    val discoveredInitialIds = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") &&
        nodeOwnsLoop(edge.obj("to").str, plan.groupId) &&
        !nodeOwnsLoop(edge.obj("from").str, plan.groupId) =>
        edge.obj("to").str
    }.toSet
    val initialIds = plan.entryInitialIds ++ discoveredInitialIds
    val externalPredecessors = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") &&
        initialIds.contains(edge.obj("to").str) &&
        !candidateCycleClosingKeys.contains(
          (edge.obj("from").str, edge.obj("to").str)
        ) &&
        !plan.continuationIds.contains(edge.obj("from").str) &&
        !nodeOwnsLoop(edge.obj("from").str, plan.groupId) =>
        edge.obj("from").str
    }.distinct

    def isRepetitionEdge(source: String, target: String): Boolean = {
      // Some lowered loop operations project to one visible call for both
      // sides of a CFG back edge.  The resulting self-edge is still a cycle
      // closure, never forward progress within the single iteration.
      if (source == target && nodeOwnsLoop(source, plan.groupId)) return true
      if (candidateCycleClosingKeys.contains((source, target)) &&
          cycleOwner(source, target).contains(plan.groupId)) return true
      val restartsAtEntry = initialIds.contains(target)
      val restartsAtGuard = plan.guardNodeIds.contains(target)
      val restartsAtBody = plan.bodyNodeIds.contains(target) && restartsAtEntry
      plan.kind match {
        case "DO" | "DO_WHILE" =>
          plan.guardNodeIds.contains(source) && restartsAtBody
        case "FOR" if plan.updateNodeIds.nonEmpty =>
          plan.updateNodeIds.contains(source) &&
            (restartsAtGuard || restartsAtBody)
        case "FOR_EACH" =>
          plan.bodyNodeIds.contains(source) &&
            (restartsAtGuard || restartsAtEntry)
        case _ =>
          plan.bodyNodeIds.contains(source) &&
            (restartsAtGuard || restartsAtEntry)
      }
    }

    val repetitionEdges = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") &&
        isRepetitionEdge(edge.obj("from").str, edge.obj("to").str) => edge
    }.toList
    val completionBoundaryEdges = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") &&
        nodeOwnsLoop(edge.obj("from").str, plan.groupId) &&
        !nodeOwnsLoop(edge.obj("to").str, plan.groupId) &&
        !nodes.get(edge.obj("from").str).exists(
          _.obj.get("type").exists(_.str == "transfer")
        ) &&
        (Set("DO", "DO_WHILE").contains(plan.kind) ||
          !plan.guardNodeIds.contains(edge.obj("from").str)) => edge
    }.toList
    val completedIterationEdges = (repetitionEdges ++ completionBoundaryEdges)
      .distinctBy(edge => (edge.obj("from").str, edge.obj("to").str))
    val repetitionKeys = completedIterationEdges.map(edge =>
      (edge.obj("from").str, edge.obj("to").str)
    ).toSet

    edges.filterInPlace { edge =>
      if (!edge.obj.get("type").exists(_.str == "sequence")) true
      else {
        val source = edge.obj("from").str
        val target = edge.obj("to").str
        val entersLoop = initialIds.contains(target) &&
          !nodeOwnsLoop(source, plan.groupId)
        val pretestGuardShortcut = !Set("DO", "DO_WHILE").contains(plan.kind) &&
          plan.guardNodeIds.contains(source) && !nodeOwnsLoop(target, plan.groupId)
        val externalZeroIteration = externalPredecessors.contains(source) &&
          plan.continuationIds.contains(target)
        !repetitionKeys.contains((source, target)) &&
          !entersLoop && !pretestGuardShortcut && !externalZeroIteration &&
          source != plan.entryNodeId && source != plan.exitNodeId
      }
    }
    externalPredecessors.foreach(source =>
      addIfSequence(source, plan.entryNodeId)
    )
    initialIds.foreach(target => addIfSequence(plan.entryNodeId, target))
    completedIterationEdges.map(_.obj("from").str).distinct.foreach(source =>
      addIfSequence(source, plan.exitNodeId)
    )
    val physicalContinuationIds = completionBoundaryEdges
      .map(_.obj("to").str).distinct
    val continuationIds =
      if (physicalContinuationIds.nonEmpty) physicalContinuationIds
      else plan.continuationIds.toList
    continuationIds.foreach(target => addIfSequence(plan.exitNodeId, target))
  }

  // Package 9: a structural transfer has exactly one outgoing route, directly
  // to the resolved target structure exit. It never reaches remaining body
  // work, a loop guard/update, or a branch's normal convergence anchor.
  val transferTargetIds = pendingStructuralTransfers.map(_.nodeId).toSet
  edges.filterInPlace { edge =>
    !edge.obj.get("type").exists(_.str == "sequence") ||
      !transferTargetIds.contains(edge.obj("from").str)
  }
  val normalizedLoopExitByGroupId = pendingLoopAnchorPlans.map(
    plan => plan.groupId -> plan.exitNodeId
  ).toMap
  pendingStructuralTransfers.foreach { transfer =>
    normalizedLoopExitByGroupId.get(transfer.targetGroupId).foreach { exitId =>
      addIfSequence(transfer.nodeId, exitId)
    }
  }

  // Package 6: convergence is a scope boundary, not a reconstructed arm
  // target. Process children before parents so a child at the end of a parent
  // arm first becomes childExit -> parentExit; the parent pass then handles
  // only its own boundary. Terminal transfers never participate.
  val ifPlanByGroupId = pendingIfAnchorPlans.map(plan => plan.groupId -> plan).toMap
  val loopExitByGroupId = pendingLoopAnchorPlans.map(
    route => route.groupId -> route.exitNodeId
  ).toMap
  val loopPlanByGroupId = pendingLoopAnchorPlans.map(
    route => route.groupId -> route
  ).toMap
  def nodeOwnsArm(nodeId: String, groupId: String, armLabel: String): Boolean =
    nodes.get(nodeId).exists { node =>
      node.obj.get("branchArms").exists(_.arr.exists { membership =>
        membership.obj("groupId").str == groupId &&
          membership.obj("armLabel").str == armLabel
      })
    }

  def nodeOwnsBranchGroup(nodeId: String, groupId: String): Boolean =
    nodes.get(nodeId).exists { node =>
      node.obj.get("branchArms").exists(_.arr.exists { membership =>
        membership.obj("groupId").str == groupId
      })
    }

  def isTerminalTransfer(nodeId: String): Boolean =
    nodes.get(nodeId).exists { node =>
      node.obj.get("type").exists(_.str == "transfer") ||
        node.obj.get("exitKind").exists { kind =>
          Set("return", "throw").contains(kind.str)
        }
    }

  // Every IF entry is a mandatory gateway. Normalize this before convergence
  // so a raw call-free-condition fork cannot be mistaken for completion of an
  // enclosing arm. For an ordinary predecessor, the entire normal CFG fork is
  // owned by the synthetic decision. Structural decisions retain their other
  // arm-selection edges and surrender only the edge entering this child.
  pendingIfAnchorPlans.foreach { plan =>
    val internalIds = (
      plan.stages.flatMap(stage => stage.conditionCallIds + stage.decisionNodeId) ++
        nodes.collect {
          case (nodeId, _) if plan.arms.exists(arm =>
            nodeOwnsArm(nodeId, plan.groupId, arm.label)) => nodeId
        }
    ).toSet
    val bypassEdges = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") &&
        internalIds.contains(edge.obj("to").str) &&
        !internalIds.contains(edge.obj("from").str) &&
        edge.obj("from").str != plan.entryNodeId => edge
    }.toList
    val existingEntrySources = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") &&
        edge.obj("to").str == plan.entryNodeId => edge.obj("from").str
    }.distinct
    val structuralExitSources = bypassEdges.map(_.obj("from").str)
      .filter { sourceId =>
        nodes.get(sourceId).exists(_.obj.get("structureRole").exists(_.str == "exit"))
      }.distinct
    val anchoredSources = (existingEntrySources ++ structuralExitSources).distinct
    val candidateCanonicalSources =
      if (anchoredSources.nonEmpty) anchoredSources
      else bypassEdges.map(_.obj("from").str).distinct
    val enclosingLoopExitIds = plan.enclosingLoopIds.flatMap(loopExitByGroupId.get).toSet
    val canonicalSources = candidateCanonicalSources.filterNot { sourceId =>
      val isEnclosingLoopEntry = nodes.get(sourceId).exists { node =>
        node.obj.get("structureRole").exists(_.str == "entry") &&
          node.obj.get("structureGroupId").exists(group =>
            plan.enclosingLoopIds.contains(group.str)
          )
      }
      !isEnclosingLoopEntry && edges.exists { edge =>
        edge.obj.get("type").exists(_.str == "sequence") &&
          edge.obj("from").str == sourceId &&
          enclosingLoopExitIds.contains(edge.obj("to").str)
      }
    }
    // Only an executable call can be Joern's implicit carrier for a call-free
    // condition fork. Structural entry/exit/decision nodes may legitimately
    // compose several routes and surrender only the concrete bypass edge.
    val exclusiveGatewaySources = canonicalSources.filter { sourceId =>
      nodes.get(sourceId).exists(_.obj.get("type").exists(_.str == "call"))
    }.toSet
    val canonicalSourceIds = canonicalSources.toSet
    val bypassKeys = bypassEdges.filter(edge =>
      canonicalSourceIds.contains(edge.obj("from").str)
    ).map(edge => (edge.obj("from").str, edge.obj("to").str)).toSet
    val removedKeys = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") && (
        exclusiveGatewaySources.contains(edge.obj("from").str) ||
          bypassKeys.contains((edge.obj("from").str, edge.obj("to").str))
      ) => (edge.obj("from").str, edge.obj("to").str)
    }.toSet

    edges.filterInPlace { edge =>
      !edge.obj.get("type").exists(_.str == "sequence") ||
        !removedKeys.contains((edge.obj("from").str, edge.obj("to").str))
    }
    structuralRouteRequirements.filterInPlace { case (key, _) =>
      !removedKeys.contains(key)
    }
    canonicalSources.foreach(source => addIfSequence(source, plan.entryNodeId))
  }

  pendingIfAnchorPlans.sortBy(_.astSize).foreach { plan =>
    plan.exitNodeId.foreach { exitId =>
      // IF arms are mutually exclusive. A raw route from one arm directly
      // into a sibling arm is an unclaimed alternative of the condition fork,
      // never normal convergence through the branch exit.
      val interArmKeys = edges.collect {
        case edge if edge.obj.get("type").exists(_.str == "sequence") &&
          plan.arms.exists(arm =>
            nodeOwnsArm(edge.obj("from").str, plan.groupId, arm.label)
          ) && nodeOwnsBranchGroup(edge.obj("to").str, plan.groupId) &&
          plan.arms.exists(arm =>
            nodeOwnsArm(edge.obj("from").str, plan.groupId, arm.label) &&
              !nodeOwnsArm(edge.obj("to").str, plan.groupId, arm.label)
          ) => (edge.obj("from").str, edge.obj("to").str)
      }.toSet
      edges.filterInPlace { edge =>
        !edge.obj.get("type").exists(_.str == "sequence") ||
          !interArmKeys.contains((edge.obj("from").str, edge.obj("to").str))
      }
      structuralRouteRequirements.filterInPlace { case (key, _) =>
        !interArmKeys.contains(key)
      }

      val boundaryCandidates = edges.collect {
        case edge if edge.obj.get("type").exists(_.str == "sequence") &&
          !isTerminalTransfer(edge.obj("from").str) &&
          plan.arms.exists { arm =>
            nodeOwnsArm(edge.obj("from").str, plan.groupId, arm.label) &&
              !nodeOwnsArm(edge.obj("to").str, plan.groupId, arm.label)
          } => edge
      }.toList
      val normalOutgoingBySource = edges.collect {
        case edge if edge.obj.get("type").exists(_.str == "sequence") => edge
      }.groupBy(_.obj("from").str)
      val boundaryEdges = boundaryCandidates.filter { boundary =>
        val source = boundary.obj("from").str
        plan.arms.find(arm => nodeOwnsArm(source, plan.groupId, arm.label))
          .exists { arm =>
            normalOutgoingBySource.getOrElse(source, Nil).forall { edge =>
              !nodeOwnsArm(edge.obj("to").str, plan.groupId, arm.label)
            }
          }
      }

      val boundaryKeys = boundaryEdges.map { edge =>
        (edge.obj("from").str, edge.obj("to").str)
      }.toSet
      edges.filterInPlace { edge =>
        if (!edge.obj.get("type").exists(_.str == "sequence")) true
        else {
          val key = (edge.obj("from").str, edge.obj("to").str)
          !boundaryKeys.contains(key) && edge.obj("from").str != exitId
        }
      }

      boundaryEdges.map(_.obj("from").str).distinct.foreach { frontierId =>
        addIfSequence(frontierId, exitId)
      }

      def canonicalContinuation(targetId: String): String =
        plan.enclosingBranchArms.lastOption.flatMap {
          case (parentGroupId, parentArmLabel) =>
            if (nodeOwnsArm(targetId, parentGroupId, parentArmLabel)) None
            else ifPlanByGroupId.get(parentGroupId).flatMap(_.exitNodeId)
        }.orElse {
          plan.enclosingLoopIds.lastOption.flatMap { loopGroupId =>
            val restartsIteration = nodes.keys.exists { sourceId =>
              val belongsToArm = plan.arms.exists(arm =>
                nodeOwnsArm(sourceId, plan.groupId, arm.label)
              )
              val isConditionSource = plan.stages.exists(
                _.conditionCallIds.contains(sourceId)
              )
              (belongsToArm || isConditionSource) &&
                candidateCycleClosingKeys.contains((sourceId, targetId))
            } || loopPlanByGroupId.get(loopGroupId).exists { loopPlan =>
              loopPlan.guardNodeIds.contains(targetId) ||
                (Set("DO", "DO_WHILE").contains(loopPlan.kind) &&
                  loopPlan.entryInitialIds.contains(targetId))
            }
            if (restartsIteration) loopExitByGroupId.get(loopGroupId)
            else if (nodeOwnsLoop(targetId, loopGroupId)) None
            else loopExitByGroupId.get(loopGroupId)
          }
        }.getOrElse(targetId)

      val physicalTargetIds = boundaryEdges.map(_.obj("to").str).distinct
      val targetsInsideEnclosingBranch = plan.enclosingBranchArms.lastOption
        .toList.flatMap { case (groupId, armLabel) =>
          physicalTargetIds.filter(nodeOwnsArm(_, groupId, armLabel))
        }
      val targetsInsideEnclosingLoop = plan.enclosingLoopIds.lastOption
        .toList.flatMap { loopGroupId =>
          physicalTargetIds.filter(nodeOwnsLoop(_, loopGroupId))
        }
      // A physical route that remains in the enclosing scope is the actual
      // continuation. Raw CFG shortcuts leaving the parent are alternatives
      // of this child condition and must not become childExit -> parentExit.
      val preferredPhysicalTargets =
        if (targetsInsideEnclosingBranch.nonEmpty) targetsInsideEnclosingBranch
        else if (targetsInsideEnclosingLoop.nonEmpty) targetsInsideEnclosingLoop
        else physicalTargetIds
      val physicalContinuations = preferredPhysicalTargets.map(
        canonicalContinuation
      ).distinct.filterNot(_ == exitId)
      val continuationIds = (
        if (physicalContinuations.nonEmpty) physicalContinuations
        else plan.continuationIds.map(canonicalContinuation)
      ).distinct.filterNot(targetId =>
        targetId == exitId || nodeOwnsBranchGroup(targetId, plan.groupId)
      )
      continuationIds.foreach(targetId => addIfSequence(exitId, targetId))

      branchGroups.find(_.obj("id").str == plan.groupId).foreach { groupValue =>
        groupValue.obj("arms").arr.foreach { armValue =>
          armValue.obj("exits").arr.foreach { exitValue =>
            if (exitValue.obj("kind").str == "continues") {
              exitValue.obj("destinationNodeId") = ujson.Str(exitId)
            }
          }
        }
      }
    }
  }

  // Convergence can create a new childExit/parentExit -> nextStructureInternal
  // edge after the raw forks were claimed above. Enforce the entry gateway a
  // second time for only those newly composed ingress edges; do not reclaim
  // complete predecessor forks in this pass.
  pendingIfAnchorPlans.foreach { plan =>
    val internalIds = (
      plan.stages.flatMap(stage => stage.conditionCallIds + stage.decisionNodeId) ++
        nodes.collect {
          case (nodeId, _) if plan.arms.exists(arm =>
            nodeOwnsArm(nodeId, plan.groupId, arm.label)) => nodeId
        }
    ).toSet
    val lateBypassEdges = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") &&
        internalIds.contains(edge.obj("to").str) &&
        !internalIds.contains(edge.obj("from").str) &&
        edge.obj("from").str != plan.entryNodeId => edge
    }.toList
    val lateBypassKeys = lateBypassEdges.map(edge =>
      (edge.obj("from").str, edge.obj("to").str)
    ).toSet
    edges.filterInPlace { edge =>
      !edge.obj.get("type").exists(_.str == "sequence") ||
        !lateBypassKeys.contains((edge.obj("from").str, edge.obj("to").str))
    }
    structuralRouteRequirements.filterInPlace { case (key, _) =>
      !lateBypassKeys.contains(key)
    }
    lateBypassEdges.map(_.obj("from").str).distinct.foreach { source =>
      addIfSequence(source, plan.entryNodeId)
    }
  }

  // After child-first convergence, an ordinary node cannot simultaneously
  // continue inside an arm and leave it. Joern can retain a lowered shortcut
  // from a child body call directly to a parent/TRY continuation (notably for
  // duplicated finally bodies). Once the child owns an internal normal route,
  // that external shortcut is not a completion frontier: claim and remove it
  // before validating the mandatory child gateway/exit contract.
  pendingIfAnchorPlans.sortBy(_.astSize).foreach { plan =>
    plan.arms.foreach { arm =>
      nodes.keys.toList.foreach { source =>
        if (nodeOwnsArm(source, plan.groupId, arm.label) &&
            !isTerminalTransfer(source)) {
          val outgoing = edges.collect {
            case edge if edge.obj.get("type").exists(_.str == "sequence") &&
              edge.obj("from").str == source => edge
          }.toList
          val internal = outgoing.filter(edge =>
            nodeOwnsArm(edge.obj("to").str, plan.groupId, arm.label)
          )
          val external = outgoing.filterNot(internal.contains)
          val genuineInternal = internal.filterNot { edge =>
            val target = edge.obj("to").str
            source == target || candidateCycleClosingKeys.contains((source, target))
          }
          val removeKeys = (
            if (genuineInternal.nonEmpty) external
            else if (internal.nonEmpty && external.nonEmpty) outgoing
            else Nil
          ).map(edge => (edge.obj("from").str, edge.obj("to").str)).toSet
          val completionTargets =
            if (genuineInternal.isEmpty && internal.nonEmpty) {
              external.map(_.obj("to").str).distinct
            } else Nil
          if (removeKeys.nonEmpty) {
            edges.filterInPlace { edge =>
              !edge.obj.get("type").exists(_.str == "sequence") ||
                !removeKeys.contains((edge.obj("from").str, edge.obj("to").str))
            }
            structuralRouteRequirements.filterInPlace { case (key, _) =>
              !removeKeys.contains(key)
            }
          }
          if (completionTargets.nonEmpty) {
            plan.exitNodeId.foreach { exitId =>
              addIfSequence(source, exitId)
              completionTargets.foreach(target => addIfSequence(exitId, target))
            }
          }
        }
      }
    }
  }

  // The cleanup above is authoritative, but retain a hard validation so a
  // future lowering shape cannot silently reintroduce a mixed frontier.
  pendingIfAnchorPlans.foreach { plan =>
    plan.arms.foreach { arm =>
      val mixedSources = nodes.keys.filter(source =>
        nodeOwnsArm(source, plan.groupId, arm.label) &&
          !isTerminalTransfer(source)
      ).filter { source =>
        val targets = edges.collect {
          case edge if edge.obj.get("type").exists(_.str == "sequence") &&
            edge.obj("from").str == source => edge.obj("to").str
        }.toList
        targets.exists(nodeOwnsArm(_, plan.groupId, arm.label)) &&
          targets.exists(target => !nodeOwnsArm(target, plan.groupId, arm.label))
      }.toList
      if (mixedSources.nonEmpty) {
        throw new IllegalStateException(
          s"Branch ${plan.groupId}.${arm.label} has mixed internal/external " +
            s"normal successors from ${mixedSources.mkString(", ")}"
        )
      }
    }
  }

  // Package 11: normalize TRY after child IF/loop structures have acquired
  // their own anchors.  The protected body is a common execution spine;
  // catch/noCatch are alternatives selected at one structural dispatch;
  // finally is common flow before the TRY exit.  Descendant anchors are
  // included in their lexical region so a child at a body/catch/finally tail
  // composes through childExit -> tryDecision/tryExit rather than bypassing
  // either structure.
  def structuralIdsInside(astNodeIds: Set[Long]): Set[String] = {
    val branchIds = discoveredBranchRegions.flatMap { region =>
      if (!astNodeIds.contains(region.structure.rootAstNodeId)) Nil
      else {
        val decisions = region.conditionStages.map(_.decisionAnchorId)
        List(region.structure.entryAnchorId, region.structure.exitAnchorId) ++ decisions
      }
    }
    val loopIds = discoveredLoopRegions.flatMap { region =>
      if (!astNodeIds.contains(region.structure.rootAstNodeId)) Nil
      else List(region.structure.entryAnchorId, region.structure.exitAnchorId)
    }
    val tryDecisionIds = pendingTryAnchorPlans.flatMap { plan =>
      discoveredBranchRegions.find(_.structure.groupId == plan.groupId).toList
        .filter(region => astNodeIds.contains(region.structure.rootAstNodeId))
        .flatMap(_ => List(plan.entryNodeId, plan.decisionNodeId, plan.exitNodeId))
    }
    (branchIds ++ loopIds ++ tryDecisionIds).toSet
  }

  def addTrySequence(
    source: String,
    target: String,
    requirements: List[(String, String)] = Nil
  ): Unit = {
    recordStructuralRouteRequirements(source, target, requirements)
    if (source != target && !edges.exists { edge =>
      edge.obj.get("type").exists(_.str == "sequence") &&
        edge.obj("from").str == source && edge.obj("to").str == target
    }) {
      edges += ujson.Obj("from" -> source, "to" -> target, "type" -> "sequence")
    }
  }

  def nodeBranchMembership(nodeId: String): List[(String, String)] =
    nodes.get(nodeId).toList.flatMap(_.obj.get("branchArms").toList)
      .flatMap(_.arr.toList).map { membership =>
        (membership.obj("groupId").str, membership.obj("armLabel").str)
      }

  pendingTryAnchorPlans.sortBy(_.astSize).foreach { plan =>
    val tryBodyIds = plan.tryBodyNodeIds ++ structuralIdsInside(plan.tryBodyAstNodeIds)
    val catchIdsByLabel = plan.catchArms.map { arm =>
      arm.label -> (arm.projectedNodeIds ++ structuralIdsInside(arm.astNodeIds))
    }.toMap
    val allCatchIds = catchIdsByLabel.values.flatten.toSet
    val finallyIds = plan.finallyNodeIds ++ structuralIdsInside(plan.finallyAstNodeIds)
    val ownAnchorIds = Set(plan.entryNodeId, plan.decisionNodeId, plan.exitNodeId)
    val completeRegionIds = tryBodyIds ++ allCatchIds ++ finallyIds ++ ownAnchorIds

    def sequenceSnapshot = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") =>
        (edge, edge.obj("from").str, edge.obj("to").str)
    }.toList

    val before = sequenceSnapshot
    val entryBoundary = before.filter { case (_, source, target) =>
      tryBodyIds.contains(target) && !completeRegionIds.contains(source)
    }
    val tryBoundaries = before.filter { case (_, source, target) =>
      tryBodyIds.contains(source) && !tryBodyIds.contains(target)
    }
    val catchBoundariesByLabel = catchIdsByLabel.map { case (label, memberIds) =>
      label -> before.filter { case (_, source, target) =>
        memberIds.contains(source) && !memberIds.contains(target)
      }
    }
    val finallyBoundaries = before.filter { case (_, source, target) =>
      finallyIds.contains(source) && !finallyIds.contains(target)
    }
    val catchEntriesByLabel = catchIdsByLabel.map { case (label, memberIds) =>
      val physical = before.collect {
        case (_, source, target)
            if memberIds.contains(target) && !memberIds.contains(source) => target
      }.distinct
      val declared = plan.catchArms.find(_.label == label).toList.flatMap(_.entryIds)
      label -> (if (physical.nonEmpty) physical else declared).distinct
    }
    val physicalFinallyEntries = before.collect {
      case (_, source, target)
          if finallyIds.contains(target) && !finallyIds.contains(source) => target
    }.distinct
    val structuralFinallyEntries = finallyIds.toList.filter { nodeId =>
      nodes.get(nodeId).exists { node =>
        node.obj.get("type").exists(_.str == "structure") &&
          node.obj.get("structureRole").exists(_.str == "entry")
      }
    }
    val rootFinallyCalls = finallyIds.toList.filter { nodeId =>
      nodes.get(nodeId).exists(_.obj.get("type").exists(_.str == "call")) &&
        !before.exists { case (_, source, target) =>
          source != target && finallyIds.contains(source) && target == nodeId
        }
    }
    val finallyEntries =
      if (physicalFinallyEntries.nonEmpty) physicalFinallyEntries
      else if (structuralFinallyEntries.nonEmpty) structuralFinallyEntries
      else rootFinallyCalls.take(1)
    // Joern lowers one copy of finally for each pending completion. After
    // projection those copies share canonical node ids, so a later copy can
    // appear as an internal edge from the finally tail back to its entry.
    // The one-execution structural contract keeps the external entry and
    // removes only these canonical-entry re-entry edges.
    val finallyReentry = before.filter { case (_, source, target) =>
      finallyIds.contains(source) && finallyEntries.contains(target)
    }

    val terminalIds = (tryBodyIds ++ allCatchIds ++ finallyIds).filter(isTerminalTransfer)
    val bodyThrowIds = terminalIds.filter { nodeId =>
      tryBodyIds.contains(nodeId) && nodes.get(nodeId).exists(
        _.obj.get("exitKind").exists(_.str == "throw")
      )
    }
    def transferTargetsStructureInsideTry(nodeId: String): Boolean =
      nodes.get(nodeId).exists { node =>
        node.obj.get("type").exists(_.str == "transfer") &&
          node.obj.get("targetStructureGroupId").exists { targetGroup =>
            normalizedLoopExitByGroupId.get(targetGroup.str).exists(tryBodyIds.contains)
          }
      }
    // A terminal throw in the protected body remains a terminal route. Catch
    // arms are presentation alternatives for exceptional execution; they are
    // not continuations of the explicit throw node. Keeping body throws in the
    // pending set also lets a finally block run before that terminal exit.
    val pendingTerminalIds = (terminalIds -- finallyIds)
      .filterNot(transferTargetsStructureInsideTry)
    val terminalIncoming = before.filter { case (_, _, target) =>
      pendingTerminalIds.contains(target) ||
        (plan.catchArms.nonEmpty && bodyThrowIds.contains(target))
    }
    val pendingTransferOutgoing = before.filter { case (_, source, _) =>
      pendingTerminalIds.contains(source) && nodes.get(source).exists(
        _.obj.get("type").exists(_.str == "transfer")
      )
    }

    val removedKeys = (
      entryBoundary ++ tryBoundaries ++ catchBoundariesByLabel.values.flatten ++
        finallyBoundaries ++ finallyReentry ++ terminalIncoming
    ).map { case (_, source, target) => (source, target) }.toSet
    edges.filterInPlace { edge =>
      if (!edge.obj.get("type").exists(_.str == "sequence")) true
      else {
        val source = edge.obj("from").str
        val target = edge.obj("to").str
        !removedKeys.contains((source, target)) &&
          !(source == target && completeRegionIds.contains(source)) &&
          source != plan.entryNodeId && source != plan.decisionNodeId &&
          source != plan.exitNodeId
      }
    }

    entryBoundary.map(_._2).distinct.foreach(source =>
      addTrySequence(source, plan.entryNodeId)
    )
    // A preceding normalized structure may already terminate at this TRY's
    // entry anchor. In that case the original outside -> body boundary has
    // deliberately disappeared, so use the CFG-discovered body entries
    // captured before any anchor rewrites.
    val physicalEntryTargets = entryBoundary.map(_._3).distinct
    val entryTargets =
      if (physicalEntryTargets.nonEmpty) physicalEntryTargets
      else plan.tryBodyEntryIds
    entryTargets.foreach(target => addTrySequence(plan.entryNodeId, target))

    val ordinaryTryFrontiers = tryBoundaries.map(_._2).distinct
      .filterNot(isTerminalTransfer)
    ordinaryTryFrontiers.foreach(source => addTrySequence(source, plan.decisionNodeId))
    terminalIncoming.foreach { case (_, source, terminalId) =>
      if (finallyEntries.nonEmpty) {
        finallyEntries.foreach(target => addTrySequence(source, target))
      } else {
        addTrySequence(source, terminalId, nodeBranchMembership(terminalId))
      }
    }

    plan.catchArms.foreach { arm =>
      catchEntriesByLabel.getOrElse(arm.label, Nil).foreach { target =>
        addTrySequence(
          plan.decisionNodeId,
          target,
          List(plan.groupId -> arm.label)
        )
      }
    }
    val noCatchTargets = if (finallyEntries.nonEmpty) finallyEntries else List(plan.exitNodeId)
    noCatchTargets.foreach(target => addTrySequence(
      plan.decisionNodeId,
      target,
      List(plan.groupId -> "noCatch")
    ))

    catchBoundariesByLabel.foreach { case (_, boundaries) =>
      boundaries.map(_._2).distinct.filterNot(isTerminalTransfer).foreach { source =>
        if (finallyEntries.nonEmpty) {
          finallyEntries.foreach(target => addTrySequence(source, target))
        } else addTrySequence(source, plan.exitNodeId)
      }
    }

    val finallyFrontiers = (finallyBoundaries.map(_._2) ++ finallyReentry.map(_._2)).distinct
      .filterNot(isTerminalTransfer)
    finallyFrontiers.foreach(source => addTrySequence(source, plan.exitNodeId))
    if (finallyEntries.nonEmpty && finallyFrontiers.nonEmpty) {
      pendingTerminalIds.foreach { terminalId =>
        finallyFrontiers.foreach(source => addTrySequence(
          source, terminalId, nodeBranchMembership(terminalId)
        ))
      }
    }

    val physicalContinuations = (
      if (finallyIds.nonEmpty) finallyBoundaries.map(_._3)
      else (tryBoundaries.map(_._3) ++ catchBoundariesByLabel.values.flatten.map(_._3))
    ).filterNot(completeRegionIds.contains).distinct
    val continuationIds = (
      if (physicalContinuations.nonEmpty) physicalContinuations
      else plan.continuationIds
    ).distinct.filterNot(completeRegionIds.contains)
    continuationIds.foreach(target => addTrySequence(plan.exitNodeId, target))

    // A transfer remains authoritative after finally: the only outgoing edge
    // of break/continue is still the target loop exit established in package
    // 9. TRY normalization only moves its incoming execution point.
    pendingTransferOutgoing.foreach { case (_, source, target) =>
      addTrySequence(source, target)
    }

    branchGroups.find(_.obj("id").str == plan.groupId).foreach { groupValue =>
      groupValue.obj("arms").arr.foreach { armValue =>
        armValue.obj("exits").arr.foreach { exitValue =>
          if (exitValue.obj("kind").str == "continues") {
            exitValue.obj("destinationNodeId") = ujson.Str(plan.exitNodeId)
          }
        }
      }
    }
  }

  // Anchor composition can replace a raw loop-closing call edge with an
  // equivalent path through one or more child structure entries/exits. Run a
  // final loop closure pass over the composed graph so no later normalization
  // can accidentally restore a next-iteration route. A cycle is eligible only
  // when every node shares an enclosing loop; non-loop cycles remain errors.
  def composedCycle(): Option[(String, String, List[String])] = {
    val outgoing = edges.collect {
      case edge if edge.obj.get("type").exists(_.str == "sequence") =>
        edge.obj("from").str -> edge.obj("to").str
    }.groupMap(_._1)(_._2)
    val active = mutable.Set[String]()
    val complete = mutable.Set[String]()
    var found: Option[(String, String, List[String])] = None
    def visit(nodeId: String, path: List[String]): Unit = {
      if (found.isEmpty && !complete.contains(nodeId)) {
        active += nodeId
        outgoing.getOrElse(nodeId, Nil).foreach { targetId =>
          if (found.isEmpty && active.contains(targetId)) {
            val completePath = path :+ nodeId
            found = Some((
              nodeId,
              targetId,
              completePath.dropWhile(_ != targetId)
            ))
          } else if (found.isEmpty) visit(targetId, path :+ nodeId)
        }
        active -= nodeId
        complete += nodeId
      }
    }
    nodes.keys.foreach(nodeId => visit(nodeId, Nil))
    found
  }

  var normalizingComposedLoops = true
  while (normalizingComposedLoops) {
    composedCycle() match {
      case Some((source, target, cycleNodes)) =>
        val sharedLoopIds = cycleNodes.map(nodeLoopIds).reduceOption(
          (left, right) => left.filter(right.contains)
        ).getOrElse(Nil)
        sharedLoopIds.lastOption.flatMap(loopExitByGroupId.get) match {
          case Some(loopExitId) =>
            edges.filterInPlace { edge =>
              !edge.obj.get("type").exists(_.str == "sequence") ||
                edge.obj("from").str != source || edge.obj("to").str != target
            }
            addIfSequence(source, loopExitId)
          case None => normalizingComposedLoops = false
        }
      case None => normalizingComposedLoops = false
    }
  }

  // A structure exit can be the first route to expose method fallthrough.
  // Materializing these endpoints after normalization prevents a later
  // structureExit -> fallthrough edge from referencing a missing node.
  pendingFallthroughPlans.foreach { plan =>
    val isUsed = edges.exists { edge =>
      edge.obj.get("to").exists(_.str == plan.nodeId)
    }
    if (isUsed) {
      addNode(plan.nodeId, ujson.Obj(
        "id" -> plan.nodeId, "type" -> "exit", "exitKind" -> "fallthrough",
        "callerMethod" -> plan.callerMethod,
        "sourceFile" -> plan.sourceFile,
        "line" -> plan.line
      ))
    }
  }

  val danglingSequenceEndpoints = edges.collect {
    case edge if edge.obj.get("type").exists(_.str == "sequence") &&
        (!nodes.contains(edge.obj("from").str) || !nodes.contains(edge.obj("to").str)) =>
      (edge.obj("from").str, edge.obj("to").str)
  }.distinct
  if (danglingSequenceEndpoints.nonEmpty) {
    throw new IllegalStateException(
      s"Normalized method graph has dangling sequence endpoints: " +
        danglingSequenceEndpoints.mkString(", ")
    )
  }

  // Package 10: validate the fully composed topology, after loop
  // normalization, structural transfers, and child-to-parent convergence.
  // Validation here is intentionally method-wide: validating individual
  // loops before nested anchors compose can miss a cycle that crosses a child
  // boundary.
  val finalSequenceEdges = edges.collect {
    case edge if edge.obj.get("type").exists(_.str == "sequence") =>
      (edge.obj("from").str, edge.obj("to").str)
  }.toList
  val finalOutgoing = finalSequenceEdges.groupMap(_._1)(_._2)
  val finalIncoming = finalSequenceEdges.groupMap(_._2)(_._1)

  val requiredEntryIds =
    pendingIfAnchorPlans.map(_.entryNodeId) ++
      pendingLoopAnchorPlans.map(_.entryNodeId) ++
      pendingTryAnchorPlans.map(_.entryNodeId)
  requiredEntryIds.distinct.foreach { entryId =>
    if (finalOutgoing.getOrElse(entryId, Nil).isEmpty) {
      throw new IllegalStateException(
        s"Structural entry $entryId must have an outgoing route"
      )
    }
  }

  val requiredExitIds =
    pendingIfAnchorPlans.flatMap(_.exitNodeId) ++
      pendingLoopAnchorPlans.map(_.exitNodeId) ++
      pendingTryAnchorPlans.map(_.exitNodeId)
  requiredExitIds.distinct.foreach { exitId =>
    if (finalIncoming.getOrElse(exitId, Nil).nonEmpty &&
        finalOutgoing.getOrElse(exitId, Nil).isEmpty) {
      throw new IllegalStateException(
        s"Reached structural exit $exitId must have an outgoing route; " +
          s"incoming=${finalIncoming.getOrElse(exitId, Nil)}; " +
          s"declaredContinuations=${pendingIfAnchorPlans.find(_.exitNodeId.contains(exitId)).toList.flatMap(_.continuationIds)}"
      )
    }
  }

  pendingStructuralTransfers.foreach { transfer =>
    val expectedTarget = normalizedLoopExitByGroupId.get(transfer.targetGroupId)
    val actualTargets = finalOutgoing.getOrElse(transfer.nodeId, Nil).distinct
    if (expectedTarget.isEmpty || actualTargets != expectedTarget.toList) {
      throw new IllegalStateException(
        s"Transfer ${transfer.nodeId} must route only to ${expectedTarget.toList}; " +
        s"actual=$actualTargets"
      )
    }
  }

  val active = mutable.Set[String]()
  val complete = mutable.Set[String]()
  var finalCycle: Option[(String, String)] = None
  var finalCyclePath: List[String] = Nil
  def validateAcyclic(nodeId: String, path: List[String] = Nil): Unit = {
    if (finalCycle.isEmpty && !complete.contains(nodeId)) {
      active += nodeId
      finalOutgoing.getOrElse(nodeId, Nil).foreach { targetId =>
        if (active.contains(targetId)) {
          finalCycle = Some((nodeId, targetId))
          val completePath = path :+ nodeId
          finalCyclePath = completePath.dropWhile(_ != targetId) :+ targetId
        } else validateAcyclic(targetId, path :+ nodeId)
      }
      active -= nodeId
      complete += nodeId
    }
  }
  nodes.keys.foreach(nodeId => validateAcyclic(nodeId))
  finalCycle.foreach { case (source, target) =>
    val method = nodes.get(source).flatMap(_.obj.get("callerMethod"))
      .map(_.str).getOrElse("<unknown>")
    throw new IllegalStateException(
      s"Normalized method graph is cyclic in $method: $source->$target; " +
        s"path=${finalCyclePath.mkString(" -> ")}"
    )
  }

  // Package 12: derive serialized requirements exactly once, after all
  // structural topology is final. Ordinary routes inherit the source node's
  // authoritative lexical membership. Structural selection and resumed
  // completion routes add only their explicit internal route facts.
  // Alternative selections sharing the same endpoints become parallel edges:
  // requirements on one edge are conjunctive, while parallel edges express
  // disjunction without inventing another structural node.
  val expandedRequirementEdges = edges.flatMap { edge =>
    if (!edge.obj.get("type").exists(_.str == "sequence")) List(edge)
    else {
      val key = (edge.obj("from").str, edge.obj("to").str)
      structuralRouteRequirements.get(key).map(_.toList.distinct) match {
        case Some(alternatives) if alternatives.nonEmpty =>
          alternatives.map { requirements =>
            val copy = ujson.Obj()
            edge.obj.foreach { case (field, value) => copy(field) = value }
            copy("_structuralRouteRequirements") = ujson.Arr(requirements.map {
              case (groupId, armLabel) => ujson.Obj(
                "groupId" -> groupId, "armLabel" -> armLabel
              )
            }*)
            copy
          }
        case _ => List(edge)
      }
    }
  }
  edges.clear()
  edges ++= expandedRequirementEdges

  val validArmsByGroup = branchGroups.map { groupValue =>
    val group = groupValue.obj
    group("id").str -> group("arms").arr.map(_("label").str).toSet
  }.toMap

  edges.foreach { edge =>
    if (edge.obj.get("type").exists(_.str == "sequence")) {
      val source = edge.obj("from").str
      val target = edge.obj("to").str
      val sourceRequirements = nodeBranchMembership(source)
      val enteredRequirements = nodeBranchMembership(target)
        .filterNot(sourceRequirements.contains)
      val routeFacts = edge.obj.get("_structuralRouteRequirements").toList
        .flatMap(_.arr.toList).map { requirement =>
          (requirement.obj("groupId").str, requirement.obj("armLabel").str)
        }

      val selectedByGroup = mutable.LinkedHashMap[String, String]()
      var impossible = false
      (sourceRequirements ++ enteredRequirements ++ routeFacts).foreach {
        case (groupId, armLabel) =>
          validArmsByGroup.get(groupId) match {
            case None =>
              throw new IllegalStateException(
                s"Sequence edge $source->$target references missing branch group $groupId"
              )
            case Some(labels) if !labels.contains(armLabel) =>
              throw new IllegalStateException(
                s"Sequence edge $source->$target references missing arm " +
                s"$groupId.$armLabel"
              )
            case _ =>
          }
          selectedByGroup.get(groupId) match {
            case Some(existingLabel) if existingLabel != armLabel =>
              impossible = true
            case None => selectedByGroup(groupId) = armLabel
            case _ =>
          }
      }
      if (impossible) edge.obj("_impossibleRequirementRoute") = ujson.Bool(true)
      else if (selectedByGroup.nonEmpty) {
        edge.obj("branchRequirements") = ujson.Arr(selectedByGroup.map {
          case (groupId, armLabel) => ujson.Obj(
            "groupId" -> groupId, "armLabel" -> armLabel
          )
        }.toSeq*)
      } else edge.obj.remove("branchRequirements")
      edge.obj.remove("_structuralRouteRequirements")
    }
  }
  edges.filterInPlace { edge =>
    !edge.obj.get("_impossibleRequirementRoute").exists(_.bool)
  }

  val deduplicatedRequirementEdges = mutable.LinkedHashMap[
    (String, String, String, List[(String, String)]),
    ujson.Obj
  ]()
  edges.foreach { edge =>
    edge.obj.remove("_impossibleRequirementRoute")
    val requirements = edge.obj.get("branchRequirements").toList
      .flatMap(_.arr.toList).map(requirement =>
        (requirement.obj("groupId").str, requirement.obj("armLabel").str)
      )
    val key = (
      edge.obj("from").str,
      edge.obj("to").str,
      edge.obj("type").str,
      requirements
    )
    deduplicatedRequirementEdges.getOrElseUpdate(key, edge)
  }
  edges.clear()
  edges ++= deduplicatedRequirementEdges.values

  val finalRequirementsByEdge = edges.collect {
    case edge if edge.obj.get("type").exists(_.str == "sequence") =>
      val requirements = edge.obj.get("branchRequirements").toList
        .flatMap(_.arr.toList).map(requirement =>
          (requirement.obj("groupId").str, requirement.obj("armLabel").str)
        ).toSet
      (edge.obj("from").str, edge.obj("to").str, requirements)
  }.toList

  // Every edge entering lexical arm content must select all memberships of
  // that target. Incoming edges to a structure exit may additionally retain
  // the arm being exited; the exit node itself owns only its enclosure.
  finalRequirementsByEdge.foreach { case (source, target, requirements) =>
    val targetMembership = nodeBranchMembership(target).toSet
    if (!targetMembership.subsetOf(requirements)) {
      throw new IllegalStateException(
        s"Sequence edge $source->$target does not select target enclosure; " +
        s"required=$requirements targetMembership=$targetMembership"
      )
    }
  }

  branchGroups.foreach { groupValue =>
    val group = groupValue.obj
    val groupId = group("id").str
    val entryId = group("entryNodeId").str
    val exitId = group.get("exitNodeId").map(_.str)
    val entryMembership = nodeBranchMembership(entryId)
    exitId.foreach { id =>
      val exitMembership = nodeBranchMembership(id)
      if (entryMembership != exitMembership) {
        throw new IllegalStateException(
          s"Branch group $groupId entry/exit enclosure differs: " +
          s"entry=$entryMembership exit=$exitMembership"
        )
      }
      finalRequirementsByEdge.foreach {
        case (source, target, requirements)
            if source == id && requirements.exists(_._1 == groupId) =>
          throw new IllegalStateException(
            s"Branch exit $id retains exited-group requirement on $source->$target"
          )
        case _ =>
      }
    }
    nodes.valuesIterator.filter { node =>
      node.obj.get("type").exists(_.str == "structure") &&
      node.obj.get("structureRole").exists(_.str == "decision") &&
      node.obj.get("structureGroupId").exists(_.str == groupId)
    }.foreach { decisionNode =>
        val decisionId = decisionNode.obj("id").str
        if (nodeBranchMembership(decisionId) != entryMembership) {
          throw new IllegalStateException(
            s"Branch decision $decisionId enclosure differs from entry $entryId"
          )
        }
    }
  }

  ujson.Obj(
    "nodes" -> nodes.values.toList,
    "edges" -> edges.toList,
    "branchGroups" -> branchGroups.toList,
    "loopGroups" -> loopGroups.toList,
    "semanticFeatures" -> semanticFeatures
  )
}

val __cfgJson: String = {
  val cfgResult = buildFullCodebaseCfg()
  ujson.write(cfgResult, indent = 2)
}
