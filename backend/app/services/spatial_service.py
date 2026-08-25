"""
Spatial Grouping Service

Groups reinforcement text entities to their nearest beam tag anchor
using the Anchor Point Method with weighted Euclidean distance and
scipy KDTree for O(log n) nearest-neighbor lookups.

Algorithm:
1. Separate parsed entities into beam tags (anchors) and reinforcement (bars/stirrups)
2. Build a KDTree spatial index from beam tag coordinates (with axis scaling)
3. For each reinforcement entity, query the KDTree for the nearest beam tag
4. Classify main bars as top/bottom based on Y-offset relative to anchor
5. Deduplicate and return assembled beams
"""

import math
from dataclasses import dataclass
from typing import List, Literal, Optional, TypedDict, Union

import numpy as np

from backend.app.models.beam import AssembledBeam
from backend.app.models.reinforcement import BeamTag, MainSteel, Stirrup


class ParsedEntity(TypedDict):
    """Typed input contract for group_texts_to_beams."""
    type: Literal["beam_tag", "main_bar", "stirrup"]
    data: Union[BeamTag, MainSteel, Stirrup]
    x: float
    y: float


@dataclass
class LocatedBeamTag:
    """A beam tag with its spatial coordinates (anchor point)."""
    tag: BeamTag
    x: float
    y: float


@dataclass
class LocatedReinforcement:
    """A reinforcement entity (bar or stirrup) with its spatial coordinates."""
    entity: Union[MainSteel, Stirrup]
    x: float
    y: float


class SpatialGrouper:
    """
    Groups reinforcement text entities to beam tags using spatial proximity.

    Uses weighted Euclidean distance where vertical distance (Y) is penalized
    more heavily than horizontal (X). This is because in structural plan drawings:
    - Beams span HORIZONTALLY (large X range within a single beam)
    - Reinforcement annotations sit VERTICALLY close to their beam (small Y offset)
    - Multiple beams can be stacked vertically on different floors

    By penalizing Y more, entities are kept within their floor/level and allowed
    to travel freely along the horizontal beam span.

    Distance formula: d = sqrt( (x_weight * dx)^2 + (y_weight * dy)^2 )
    """

    def __init__(self, x_weight: float = 1.0, y_weight: float = 2.0):
        """
        Initialize the spatial grouper with axis weights.

        Args:
            x_weight: Multiplier for horizontal distance. Default 1.0.
            y_weight: Multiplier for vertical distance. Default 2.0.
                      Higher value keeps entities locked to their vertical level,
                      preventing cross-floor/cross-beam assignment.
        """
        self.x_weight = x_weight
        self.y_weight = y_weight

    def group_texts_to_beams(
        self,
        parsed_entities: List[ParsedEntity],
    ) -> List[AssembledBeam]:
        """
        Group parsed reinforcement entities to their nearest beam tag.

        Args:
            parsed_entities: List of typed dicts with keys:
                - "type": "beam_tag" | "main_bar" | "stirrup"
                - "data": BeamTag | MainSteel | Stirrup
                - "x": float
                - "y": float

        Returns:
            List of AssembledBeam objects with reinforcement grouped
            and sorted into top/bottom positions.
        """
        # Step 1: Separate entities
        anchors: List[LocatedBeamTag] = []
        reinforcements: List[LocatedReinforcement] = []

        for entity in parsed_entities:
            entity_type = entity.get("type")
            data = entity.get("data")
            x = float(entity.get("x", 0.0))
            y = float(entity.get("y", 0.0))

            if entity_type == "beam_tag" and isinstance(data, BeamTag):
                anchors.append(LocatedBeamTag(tag=data, x=x, y=y))
            elif entity_type == "main_bar" and isinstance(data, MainSteel):
                reinforcements.append(LocatedReinforcement(entity=data, x=x, y=y))
            elif entity_type == "stirrup" and isinstance(data, Stirrup):
                reinforcements.append(LocatedReinforcement(entity=data, x=x, y=y))

        if not anchors:
            return []

        # Step 2: Build KDTree spatial index with weighted coordinates
        anchor_coords_scaled = np.array([
            [a.x * self.x_weight, a.y * self.y_weight] for a in anchors
        ])

        try:
            from scipy.spatial import KDTree
            tree = KDTree(anchor_coords_scaled)
            use_kdtree = True
        except ImportError:
            use_kdtree = False

        # Step 3: Assign each reinforcement to its nearest anchor
        assignments: dict[int, List[LocatedReinforcement]] = {
            i: [] for i in range(len(anchors))
        }

        if use_kdtree and reinforcements:
            reinf_coords_scaled = np.array([
                [r.x * self.x_weight, r.y * self.y_weight] for r in reinforcements
            ])
            _, indices = tree.query(reinf_coords_scaled)
            for i, reinf in enumerate(reinforcements):
                assignments[int(indices[i])].append(reinf)
        else:
            anchor_coords = [(a.x, a.y) for a in anchors]
            for reinf in reinforcements:
                nearest_idx = self._find_nearest_anchor_brute(
                    reinf.x, reinf.y, anchor_coords
                )
                assignments[nearest_idx].append(reinf)

        # Step 4: Build AssembledBeam objects with top/bottom classification
        assembled_beams: List[AssembledBeam] = []

        for idx, anchor in enumerate(anchors):
            top_bars: List[MainSteel] = []
            bottom_bars: List[MainSteel] = []
            stirrups: List[Stirrup] = []

            # Dead zone: text within beam depth/2 of anchor is ambiguous.
            # Use a threshold based on beam depth to avoid misclassification
            # of labels sitting inside the beam cross-section.
            dead_zone = anchor.tag.depth / 4.0  # quarter-depth tolerance

            seen_bars: set[tuple[int, int]] = set()  # (bar_count, diameter) for dedup
            seen_stirrups: set[tuple[Optional[int], int]] = set()  # (diameter, spacing)

            for reinf in assignments[idx]:
                if isinstance(reinf.entity, MainSteel):
                    key = (reinf.entity.bar_count, reinf.entity.diameter)
                    if key in seen_bars:
                        continue
                    seen_bars.add(key)

                    y_offset = reinf.y - anchor.y
                    if y_offset > dead_zone:
                        top_bars.append(reinf.entity)
                    else:
                        bottom_bars.append(reinf.entity)

                elif isinstance(reinf.entity, Stirrup):
                    key = (reinf.entity.diameter, reinf.entity.spacing)
                    if key in seen_stirrups:
                        continue
                    seen_stirrups.add(key)
                    stirrups.append(reinf.entity)

            assembled_beams.append(AssembledBeam(
                beam_tag=anchor.tag,
                top_main_bars=top_bars,
                bottom_main_bars=bottom_bars,
                stirrups=stirrups,
            ))

        return assembled_beams

    def _find_nearest_anchor_brute(
        self,
        x: float,
        y: float,
        anchor_coords: List[tuple[float, float]],
    ) -> int:
        """
        Fallback brute-force nearest-anchor search (when scipy unavailable).

        Complexity: O(n) per query.
        """
        min_distance = float("inf")
        nearest_idx = 0

        for idx, (ax, ay) in enumerate(anchor_coords):
            dx = self.x_weight * (x - ax)
            dy = self.y_weight * (y - ay)
            distance = math.hypot(dx, dy)

            if distance < min_distance:
                min_distance = distance
                nearest_idx = idx

        return nearest_idx

    @classmethod
    def from_dwg_entities(
        cls,
        entities: list,
        x_weight: float = 1.0,
        y_weight: float = 2.0,
    ) -> List[AssembledBeam]:
        """
        Convenience factory: parse DWGTextEntity objects and group them in one call.

        Bridges Phase 2 (parsing) and Phase 3 (spatial grouping).

        Args:
            entities: List of DWGTextEntity objects (from extract_text_from_dwg).
            x_weight: Horizontal distance weight.
            y_weight: Vertical distance weight.

        Returns:
            List of AssembledBeam objects.
        """
        from backend.app.services.parser_service import ReinforcementParser

        parsed_entities: List[ParsedEntity] = []

        for entity in entities:
            text = entity.text if hasattr(entity, "text") else entity.get("text", "")
            x = entity.x if hasattr(entity, "x") else entity.get("x", 0.0)
            y = entity.y if hasattr(entity, "y") else entity.get("y", 0.0)

            beam_tag = ReinforcementParser.parse_beam_tag(text)
            if beam_tag:
                parsed_entities.append({"type": "beam_tag", "data": beam_tag, "x": x, "y": y})
                continue

            main_bar = ReinforcementParser.parse_main_bar(text)
            if main_bar:
                parsed_entities.append({"type": "main_bar", "data": main_bar, "x": x, "y": y})
                continue

            stirrup = ReinforcementParser.parse_stirrup(text)
            if stirrup:
                parsed_entities.append({"type": "stirrup", "data": stirrup, "x": x, "y": y})

        grouper = cls(x_weight=x_weight, y_weight=y_weight)
        return grouper.group_texts_to_beams(parsed_entities)
