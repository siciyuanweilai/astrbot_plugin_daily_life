from .attention import FocusArchiveMixin
from .clips import ClipArchiveMixin
from .choice import LifeDecisionArchiveMixin
from .emotions import EmotionArchiveMixin
from .episodes import EpisodeArchiveMixin
from .lexicon import LexiconArchiveMixin
from .maintenance import MaintenanceArchiveMixin
from .patterns import BehaviorArchiveMixin
from .proofs import EvidenceArchiveMixin
from .reactions import FeedbackArchiveMixin
from .cadence import PhysiologicalRhythmArchiveMixin
from .voices import ExpressionArchiveMixin


class ExperienceArchiveMixin(
    EpisodeArchiveMixin,
    LifeDecisionArchiveMixin,
    EmotionArchiveMixin,
    PhysiologicalRhythmArchiveMixin,
    EvidenceArchiveMixin,
    FeedbackArchiveMixin,
    ExpressionArchiveMixin,
    ClipArchiveMixin,
    BehaviorArchiveMixin,
    FocusArchiveMixin,
    LexiconArchiveMixin,
    MaintenanceArchiveMixin,
):
    pass


__all__ = ["ExperienceArchiveMixin"]
