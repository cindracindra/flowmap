from joern.joern_session import JoernSession
from domain.util import is_jdk_builtin, is_noise

def get_all_methods(session: JoernSession) -> list[str]:
    """
    Returns a list of all method names.
    """
    return session.query_json("cpg.method.fullName.toList")


def _filter_methods(fullnames: list[str]) -> list[str]:
    """
    Drops noise and JDK built-ins from a list of method fullNames.
    """
    return [
        fullname
        for fullname in fullnames
        if not is_noise(fullname) and not is_jdk_builtin(fullname)
    ]


def get_filtered_methods(session: JoernSession) -> list[str]:
    """
    Returns a list of method names with noise and JDK built-ins filtered out.
    """
    return _filter_methods(get_all_methods(session))


def find_methods_by_name(session: JoernSession, name: str) -> list[str]:
    """
    Returns a list of method fullNames matching the given method name, with
    noise and JDK built-ins filtered out.
    """
    matches = session.query_json(f'cpg.method.name("{name}").fullName.toList')
    return _filter_methods(matches)
