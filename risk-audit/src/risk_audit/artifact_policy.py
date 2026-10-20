"""定义审核产物的持久化策略。"""

from enum import Enum


class ArtifactPolicy(str, Enum):
    """控制审核过程需要持久化的产物范围。"""

    DETAILED = "detailed"
    MINIMAL = "minimal"
