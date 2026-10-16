"""Contour-specific session setup and two-handle interaction."""

from dataclasses import replace

import numpy as np

from .compose import TailPose
from .contour_compose import ContourComposer
from .contour_tail_curve import bend_from_point


def rebuild_contour(session):
    data = session.data
    composer = ContourComposer(
        session.body_rgba,
        session.tail_rgba,
        data.attachment_guide,
        data.tail_rig,
        data.compositing_recipe,
        session.canvas_size,
    )
    composer.fidelity = session.fidelity
    composer.tip_fidelity = session.tip_fidelity
    session.data = replace(data, attachment_guide=composer.guide)
    if not getattr(session, "_contour_registered", False):
        rig = data.tail_rig.rig_space()
        base = np.asarray(rig.base_centre) + rig.origin - np.asarray(composer.guide.origin)
        u = composer.guide.nearest_u(tuple(base))
        tip = composer.geometry.clamp_tip(u, session.pose.tip)
        session.pose = TailPose(u, tip, session.pose.bend)
        session._contour_registered_u = u
        session._contour_registered = True
    # Rescaling rebuilds registration from source-space raster data. Restore
    # the contour's normalized root for Reset without changing the live pose.
    u = session._contour_registered_u
    tip = composer.geometry.clamp_tip(u, data.registered_pose.tip)
    session.data = replace(
        session.data, registered_pose=replace(data.registered_pose, root_u=u, tip=tip)
    )
    return composer


def drag_contour(session, point):
    geometry = session.composer.geometry
    guide = geometry.guide
    if session._drag["kind"] == "root":
        local = tuple(np.asarray(point) - guide.origin)
        u = guide.nearest_u(local, u0=session.pose.root_u, window=0.12)
        # Translate from drag-start coordinates, avoiding accumulated drift.
        pose0 = session._drag["pose0"]
        delta = np.asarray(guide.point_at(u)) - guide.point_at(pose0.root_u)
        tip = tuple(np.asarray(pose0.tip) + delta)
        session.pose = TailPose(u, tip, session.pose.bend)
    elif session._drag["kind"] == "middle":
        bend = bend_from_point(geometry, session.pose, point)
        session.pose = TailPose(session.pose.root_u, session.pose.tip, bend)
    else:
        tip = geometry.clamp_tip(session.pose.root_u, point)
        session.pose = TailPose(session.pose.root_u, tip, session.pose.bend)
    session.update_preview()
