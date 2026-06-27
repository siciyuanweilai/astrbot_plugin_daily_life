from .attention import FocusArchiveMixin
from .episodes import EpisodeArchiveMixin
from .lexicon import LexiconArchiveMixin
from .maintenance import MaintenanceArchiveMixin
from .patterns import BehaviorArchiveMixin
from .proofs import EvidenceArchiveMixin
from .reactions import FeedbackArchiveMixin
from .voices import ExpressionArchiveMixin


class ExperienceArchiveMixin(
    EpisodeArchiveMixin,
    EvidenceArchiveMixin,
    FeedbackArchiveMixin,
    ExpressionArchiveMixin,
    BehaviorArchiveMixin,
    FocusArchiveMixin,
    LexiconArchiveMixin,
    MaintenanceArchiveMixin,
):
    pass


__all__ = ["ExperienceArchiveMixin"]
