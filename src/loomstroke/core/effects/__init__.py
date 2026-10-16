"""Legacy image-effect imports; implementations live in :mod:`core.filters`."""

from ..filters import FILTER_LAYER_TYPE as FILTER_LAYER_TYPE
from ..filters import BaseFilter as BaseFilter
from ..filters import BrightnessContrastFilter as BrightnessContrastFilter
from ..filters import CurvesFilter as CurvesFilter
from ..filters import FilterParamSchema as FilterParamSchema
from ..filters import FilterRegistry as FilterRegistry
from ..filters import GaussianBlurFilter as GaussianBlurFilter
from ..filters import GradientMapFilter as GradientMapFilter
from ..filters import HueSaturationFilter as HueSaturationFilter
from ..filters import InvertFilter as InvertFilter
from ..filters import LevelsFilter as LevelsFilter
from ..filters import MangaScreentoneFilter as MangaScreentoneFilter
from ..filters import MosaicFilter as MosaicFilter
from ..filters import NoiseReductionFilter as NoiseReductionFilter
from ..filters import SamplerUniform as SamplerUniform
from ..filters import SharpenFilter as SharpenFilter

BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
EffectRegistry = FilterRegistry
GaussianBlurEffect = GaussianBlurFilter
LevelsEffect = LevelsFilter
SharpenEffect = SharpenFilter
NoiseReductionEffect = NoiseReductionFilter
HueSaturationEffect = HueSaturationFilter
InvertEffect = InvertFilter
BrightnessContrastEffect = BrightnessContrastFilter
CurvesEffect = CurvesFilter
GradientMapEffect = GradientMapFilter
MosaicEffect = MosaicFilter
MangaScreentoneEffect = MangaScreentoneFilter

__all__ = [
    "FILTER_LAYER_TYPE",
    "BaseEffect",
    "BaseFilter",
    "BrightnessContrastEffect",
    "BrightnessContrastFilter",
    "CurvesEffect",
    "CurvesFilter",
    "EffectParamSchema",
    "EffectRegistry",
    "FilterParamSchema",
    "FilterRegistry",
    "GaussianBlurEffect",
    "GaussianBlurFilter",
    "GradientMapEffect",
    "GradientMapFilter",
    "HueSaturationEffect",
    "HueSaturationFilter",
    "InvertEffect",
    "InvertFilter",
    "LevelsEffect",
    "LevelsFilter",
    "MangaScreentoneEffect",
    "MangaScreentoneFilter",
    "MosaicEffect",
    "MosaicFilter",
    "NoiseReductionEffect",
    "NoiseReductionFilter",
    "SamplerUniform",
    "SharpenEffect",
    "SharpenFilter",
]
