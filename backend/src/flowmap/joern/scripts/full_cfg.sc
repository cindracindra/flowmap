import scala.collection.mutable


def buildFullCodebaseCfg(): ujson.Obj = {
  val nodes = mutable.LinkedHashMap[String, ujson.Obj]()
  val edges = mutable.ArrayBuffer[ujson.Obj]()
  val branchGroups = mutable.ArrayBuffer[ujson.Obj]()

  // (groupId, armLabel) pairs per call id.
  type ArmTags = mutable.Map[Long, mutable.ArrayBuffer[(String, String)]]

  def addNode(id: String, obj: ujson.Obj): Unit = {
    if (!nodes.contains(id)) nodes(id) = obj
  }

  // Distinguishes an explicit `return` from an implicit end-of-method,
  // for a call that has NO further call ahead of it. 
  def classifyTerminus(startPoints: List[CfgNode]): String = {
    val seen = mutable.Set[Long]()
    var frontier = startPoints
    while (frontier.nonEmpty) {
      val n = frontier.head
      frontier = frontier.tail
      if (!seen.contains(n.id)) {
        seen += n.id
        if (n.label == "RETURN") return "return"
        frontier = frontier ++ n.start.cfgNext.l
      }
    }
    "fallthrough"
  }

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

  // Check how branch arm ends - throw, return or continues
  def armTerminus(armRoot: AstNode, armCalls: List[Call]): String = {
    if (armCalls.exists(_.methodFullName == "<operator>.throw")) "throw"
    else if (armRoot.ast.collectAll[Return].nonEmpty) "return"
    else "continues"
  }

  // Create a branch arm object based on certain branch entry point/
  // arm root
  def addArm(
    groupId: String, label: String, conditionCode: Option[String],
    armRoot: AstNode, arms: ujson.Arr, armTags: ArmTags
  ): Unit = {
    val armCalls = armRoot.ast.isCall.l
    armCalls.foreach { c =>
      armTags.getOrElseUpdate(c.id, mutable.ArrayBuffer()) += ((groupId, label))
    }
    val armObj = ujson.Obj(
      "label" -> label,
      "empty" -> ujson.Bool(armCalls.isEmpty),
      "terminus" -> armTerminus(armRoot, armCalls)
    )
    conditionCode.foreach { cc => armObj("conditionCode") = ujson.Str(cc) }
    armCalls.headOption.foreach { c => armObj("firstCallId") = ujson.Str(s"c${c.id}") }
    arms.arr.addOne(armObj)
  }

  // Add explicit empty arm for `if` with no `else`.
  def addImplicitElse(arms: ujson.Arr): Unit = {
    arms.arr.addOne(ujson.Obj(
      "label" -> "else",
      "empty" -> ujson.Bool(true),
      "terminus" -> ujson.Str("continues")
    ))
  }

  // Emits ONE group for a whole if / else-if / else chain.
  def emitIfChain(head: ControlStructure, methodFullName: String, armTags: ArmTags): Unit = {
    val groupId = s"cs${head.id}"
    val arms = ujson.Arr()
    var current = head
    var idx = 0
    var walking = true
    while (walking) {
      val conditionId = current.condition.headOption.map(_.id)
      val armRoots = current.astChildren.l.filterNot(c => conditionId.contains(c.id))
      val label = if (idx == 0) "if" else s"elseif$idx"
      armRoots.headOption.foreach { thenRoot =>
        addArm(groupId, label, current.condition.headOption.map(_.code), thenRoot, arms, armTags)
      }
      elseChainNext(current) match {
        case Some(next) =>
          current = next
          idx += 1
        case None =>
          val chainEndsWithElse = armRoots.size > 1
          if (chainEndsWithElse) {
            addArm(groupId, "else", None, armRoots.last, arms, armTags)
          } else {
            addImplicitElse(arms)
          }
          walking = false
      }
    }
    branchGroups += ujson.Obj(
      "id" -> groupId, "kind" -> "IF",
      "method" -> methodFullName,
      "line" -> head.lineNumber.getOrElse(-1),
      "arms" -> arms
    )
  }

  // Emits ONE group for try block
  def emitTryGroup(cs: ControlStructure, methodFullName: String, armTags: ArmTags): Unit = {
    val groupId = s"cs${cs.id}"
    val armRoots = cs.astChildren.l
    val arms = ujson.Arr()
    var catchIdx = 0
    armRoots.zipWithIndex.foreach { case (armRoot, idx) =>
      val structureType = armRoot match {
        case c: ControlStructure => c.controlStructureType
        case _                   => ""
      }
      val label = structureType match {
        case "CATCH"   => catchIdx += 1; s"catch$catchIdx"
        case "FINALLY" => "finally"
        case _         => if (idx == 0) "try" else s"arm$idx"
      }
      addArm(groupId, label, None, armRoot, arms, armTags)
    }
    branchGroups += ujson.Obj(
      "id" -> groupId, "kind" -> "TRY",
      "method" -> methodFullName,
      "line" -> cs.lineNumber.getOrElse(-1),
      "arms" -> arms
    )
  }

  cpg.method.isExternal(false).whereNot(_.isAbstract).l.foreach { method =>
    val entryId = s"m${method.id}"
    addNode(entryId, ujson.Obj(
      "id" -> entryId, "type" -> "entry",
      "calleeFullName" -> method.fullName, "line" -> method.lineNumber.getOrElse(-1)
    ))

    val methodCalls = method.call.l

    val nextCallsById: Map[Long, List[Call]] =
      methodCalls.map(c => c.id -> c.start.repeat(_.cfgNext)(_.until(_.isCall)).isCall.l).toMap

    val terminusById: Map[Long, String] = methodCalls.flatMap { c =>
      val nextCalls = nextCallsById(c.id)
      val value =
        if (nextCalls.nonEmpty && nextCalls.forall(_.methodFullName == "<operator>.throw")) Some("throw")
        else if (nextCalls.isEmpty) Some(classifyTerminus(c.start.cfgNext.l))
        else None
      value.map(v => c.id -> v)
    }.toMap

    // Branch-group capture: runs before the node pass so every call in
    // an arm can be tagged when its node is built. Chain heads only --
    // an IF that is some other IF's else-chain continuation is folded
    // into that chain's group instead of getting one of its own.
    val armTags: ArmTags = mutable.Map()
    val controlStructures = method.controlStructure.l
    val ifs = controlStructures.filter(_.controlStructureType == "IF")
    val chainedIfIds = ifs.flatMap(cs => elseChainNext(cs).map(_.id)).toSet

    // Each group records its owning method: a group is method-level
    // metadata with no node of its own, so nothing else identifies where
    // it lives once the graph is sliced (a group whose every arm is empty
    // has no tagged node to recover it from either).
    ifs.filterNot(cs => chainedIfIds.contains(cs.id)).foreach { head =>
      emitIfChain(head, method.fullName, armTags)
    }
    controlStructures.filter(_.controlStructureType == "TRY").foreach { cs =>
      emitTryGroup(cs, method.fullName, armTags)
    }

    // first call(s) reached from method entry, skipping non-call nodes
    method.start.repeat(_.cfgNext)(_.until(_.isCall)).isCall.l.foreach { fc =>
      edges += ujson.Obj("from" -> entryId, "to" -> s"c${fc.id}", "type" -> "sequence")
    }

    methodCalls.foreach { call =>
      val callId = s"c${call.id}"

      // intra-method sequence: next call(s) after this one
      nextCallsById(call.id).foreach { nc =>
        edges += ujson.Obj("from" -> callId, "to" -> s"c${nc.id}", "type" -> "sequence")
      }

      // Same ujson.Obj-and-update pattern as addArm above -- see the note
      // there on why a scala.collection.mutable.LinkedHashMap can't be
      // handed to ujson.Obj(...).
      val callNode = ujson.Obj(
        "id" -> callId, "type" -> "call",
        "callerMethod" -> method.fullName, "calleeFullName" -> call.methodFullName,
        "code" -> call.code, "line" -> call.lineNumber.getOrElse(-1)
      )
      armTags.get(call.id).foreach { tags =>
        val tagArr = ujson.Arr()
        tags.foreach { case (groupId, label) =>
          tagArr.arr.addOne(ujson.Obj("groupId" -> ujson.Str(groupId), "armLabel" -> ujson.Str(label)))
        }
        callNode("branchArms") = tagArr
      }
      terminusById.get(call.id).foreach { t => callNode("terminus") = ujson.Str(t) }
      addNode(callId, callNode)

      // data dependency chain -- same ddgIn walk as inter_cfg.sc, unfiltered
      call.start.repeat(_.ddgIn)(_.maxDepth(10).until(_.isCall)).isCall.dedup.l.foreach { src =>
        edges += ujson.Obj("from" -> s"c${src.id}", "to" -> callId, "type" -> "data")
      }

      // interprocedural traversal
      val calleeMethods = call.callee.whereNot(_.isAbstract)
        .filter(m => m.isExternal || m.block.astChildren.nonEmpty).l
      if (calleeMethods.isEmpty) {
        addNode(callId + "_unresolved", ujson.Obj(
          "id" -> (callId + "_unresolved"), "type" -> "leaf", "reason" -> "unresolved"
        ))
        edges += ujson.Obj("from" -> callId, "to" -> (callId + "_unresolved"), "type" -> "invoke")
      } else {
        calleeMethods.foreach { callee =>
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
    }
  }

  ujson.Obj(
    "nodes" -> nodes.values.toList,
    "edges" -> edges.toList,
    "branchGroups" -> branchGroups.toList
  )
}

val __cfgJson: String = {
  val cfgResult = buildFullCodebaseCfg()
  ujson.write(cfgResult, indent = 2)
}
