import io.shiftleft.semanticcpg.language._
import scala.collection.mutable

def stripQuotes(s: String): String = {
  val noPrefix = if (s.startsWith("\"")) s.substring(1) else s
  if (noPrefix.endsWith("\"")) noPrefix.substring(0, noPrefix.length - 1) else noPrefix
}

def buildClassDocuments(
  angleBracketMarkers: Set[String],
  jdkPrefixes: Set[String],
  accessorPrefix: String,
  lambdaRegex: String,
  anonRegex: String
): ujson.Obj = {

  def isBracketedOrJdk(fullName: String): Boolean =
    angleBracketMarkers.exists(fullName.contains) || jdkPrefixes.exists(fullName.startsWith)

  val lambdaPattern = lambdaRegex.r
  val anonPattern = anonRegex.r

  def isSynthetic(name: String): Boolean =
    angleBracketMarkers.exists(name.contains) ||
    name.startsWith(accessorPrefix) ||
    lambdaPattern.findFirstIn(name).isDefined ||
    anonPattern.findFirstIn(name).isDefined

  val methodDocs = mutable.ArrayBuffer[ujson.Obj]()

  val classes = cpg.typeDecl
    .filterNot(_.isExternal)
    .filterNot(td => isSynthetic(td.name) || isSynthetic(td.fullName))
    .filterNot(td => isBracketedOrJdk(td.fullName))
    .map { td =>
      val pkg = td.fullName
        .split("\\.")
        .dropRight(1)
        .mkString(".")

      val methods = td.method
        .filterNot(m => isSynthetic(m.name))
        .name.dedup.l

      td.method
        .filterNot(m => isSynthetic(m.name) && m.name != "<init>")
        .foreach { m =>
          val mIdentifiers = m.ast.isIdentifier
            .filterNot(i => isSynthetic(i.name) || i.name == "this" || i.name.startsWith("$"))
            .name.dedup.l
          val mLiterals = m.ast.isLiteral
            .filter(_.typeFullName == "java.lang.String")
            .code.map(stripQuotes).l
          val mInternalCalls = m.call.callee.filterNot(_.isExternal).fullName.dedup.l

          methodDocs += ujson.Obj(
            "fullName" -> m.fullName,
            "terms" -> ujson.Arr(
              (Seq(m.name) ++ mIdentifiers ++ mLiterals ++ mInternalCalls)
                .map(ujson.Str(_)): _*
            )
          )
        }

      val members = td.member
        .filterNot(m => isSynthetic(m.name))
        .name.dedup.l

      val identifiers = td.method.ast.isIdentifier
        .filterNot(i => isSynthetic(i.name) || i.name == "this" || i.name.startsWith("$"))
        .name.dedup.l

      val comments = cpg.file.filter(_.name == td.filename).comment.code.dedup.l

      val literals = td.method.ast.isLiteral
        .filter(_.typeFullName == "java.lang.String")
        .code.map(stripQuotes).dedup.l

      // Annotation names expose architectural role without reading the body:
      // @Controller, @Repository, @Service, @Entity are stronger layer
      // signals than any set of method names for placing a class in a
      // functional area.
      val annotations = td.annotation.name.dedup.l

      val SKIP_INHERITS = Set(
        "Object", "Serializable", "Cloneable", "Enum",
        "Exception", "RuntimeException", "Throwable"
      )
      val inherits = td.inheritsFromTypeFullName
        .map(_.split("\\.").last)
        .filterNot(n => SKIP_INHERITS.contains(n) || isSynthetic(n))
        .dedup.l

      ujson.Obj(
        "className"  -> td.name,
        "fullName"   -> td.fullName,
        "package"    -> pkg,
        "filename"   -> td.filename,
        "methodNames" -> ujson.Arr(methods.map(ujson.Str(_)): _*),
        "memberNames" -> ujson.Arr(members.map(ujson.Str(_)): _*),
        "annotations" -> ujson.Arr(annotations.map(ujson.Str(_)): _*),
        "inherits"    -> ujson.Arr(inherits.map(ujson.Str(_)): _*),
        "identifiers" -> ujson.Arr(identifiers.map(ujson.Str(_)): _*),
        "comments"    -> ujson.Arr(comments.map(ujson.Str(_)): _*),
        "literals"    -> ujson.Arr(literals.map(ujson.Str(_)): _*),
        "terms"       -> ujson.Arr(
          (Seq(td.name) ++ methods ++ members ++ annotations ++ inherits
            ++ identifiers ++ comments ++ literals)
            .map(ujson.Str(_)): _*
        )
      )
    }
    .l

  ujson.Obj("classes" -> classes, "methods" -> methodDocs.toList)
}

val __classDocsJson: String = {
  val result = buildClassDocuments(
    Set(ANGLE_BRACKET_MARKERS_PLACEHOLDER),
    Set(JDK_PREFIXES_PLACEHOLDER),
    ACCESSOR_PREFIX_PLACEHOLDER,
    LAMBDA_REGEX_PLACEHOLDER,
    ANON_REGEX_PLACEHOLDER
  )
  ujson.write(result, indent = 2)
}
