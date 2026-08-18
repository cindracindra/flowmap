_LABEL_SYSTEM_PROMPT = (
    "You are labeling a cluster of related Java classes from a codebase. "
    "Given representative terms and class names, respond with ONLY a "
    "short 2-4 word feature label (e.g. 'Account Management', 'Payment "
    "Processing'). No explanation, no punctuation beyond spaces."
)

_WHOLE_CORPUS_SYSTEM_PROMPT = (
    "You are analyzing a Java codebase to group its classes into "
    "thematic FEATURE groups -- what the application actually DOES for "
    "its users/domain (e.g. 'Account Management', 'Payment Processing'), "
    "not how it's built. You are given each class's fully-qualified name "
    "and structured representative evidence such as methods, members, "
    "annotations, inherited types, identifiers, comments, and string "
    "literals.\n\n"
    "Do NOT create a group for, and do NOT include in any group, any class "
    "or pseudo-class that does not independently represent user/domain "
    "functionality:\n"
    "- an application entry point / bootstrap class (e.g. Main, "
    "Application, or any class whose only role is wiring/starting the app)\n"
    "- a custom exception class (its role is error signaling, not a "
    "feature in itself)\n"
    "- a generic, domain-agnostic utility/helper class with no "
    "business-specific behavior (e.g. math helpers, string helpers, "
    "logging wrappers)\n"
    "- a compiler-generated or synthetic artifact, such as lambda "
    "implementation method or pseudo-type (e.g. names containing '<lambda>' "
    "or 'lambda$'). A lambda is an implementation detail of its enclosing "
    "class, not a standalone feature. Omit it even when its terms appear "
    "business-specific\n"
    "Simply omit these classes from every group -- do not invent an "
    "'Infrastructure' or 'Utilities' catch-all group for them either; "
    "leaving a class out of every group is the correct way to exclude it.\n\n"
    "Group the remaining, genuinely feature-bearing classes by shared "
    "purpose; a class that doesn't clearly belong with others may still "
    "be its own group if it represents a real distinct feature. Respond "
    "with ONLY valid JSON, no other text, matching this shape exactly: "
    '{"groups": [{"label": "short label", '
    '"member_full_names": ["fully.qualified.Name", ...]}]}'
)

_CLASSIFY_OPERATION_SYSTEM_PROMPT = (
    "You are assigning ONE operation -- a call chain through a Java "
    "codebase, rooted at one entry point -- to the closest-matching "
    "feature group from an existing list. You are given each group's id, "
    "label, and member classes (each listed as \"[id] label: members\"), "
    "plus the operation's own methods with representative terms from "
    "each.\n\n"
    "If the operation genuinely doesn't fit any group -- e.g. it is purely "
    "infrastructure/bootstrap/logging with no real feature behavior -- "
    "use null instead of forcing a match.\n\n"
    "Respond with ONLY valid JSON, no other text, no explanation, matching "
    'this shape exactly: {"group_id": <integer id from the list, or null>}'
)

_LABEL_OPSEQ_SYSTEM_PROMPT = (
    "You are naming ONE specific operation -- a single call chain through "
    "a Java codebase, rooted at one entry point -- with a short 2-4 word "
    "operational label (e.g. 'Fund Transfer', 'Password Reset', 'Order "
    "Checkout').\n\n"
    "You are always given the operation's own methods, with representative "
    "terms from each. You may ALSO be given the broader feature group "
    "(cluster) this operation was already assigned to -- its id, label, "
    "and member classes.\n\n"
    "If a cluster IS given: the cluster label names the broader feature "
    "area; your label must be MORE SPECIFIC than it, describing what THIS "
    "operation actually does within that area based on its own methods -- "
    "never just repeat or rephrase the cluster label itself.\n"
    "If NO cluster is given: name what the operation does based solely on "
    "its own methods.\n\n"
    "Respond with ONLY the short 2-4 word label. No explanation, no "
    "punctuation beyond spaces."
)
