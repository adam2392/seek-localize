from os import path as op
from pathlib import Path
from typing import Union
import nibabel as nb
import numpy as np
from mne import read_talxfm
from mne.source_space import _read_mri_info
from mne.utils import warn
from mne_bids import get_entities_from_fname
from nibabel.affines import apply_affine

from seek_localize import Sensors
from seek_localize.config import MAPPING_COORD_FRAMES
from seek_localize.utils import _scale_coordinates


def convert_coord_space(
    sensors: Sensors, to_coord: str, subjects_dir: str = None, verbose: bool = True
):
    """Convert electrode voxel coordinates between coordinate systems.

    To obtain the sensors, one can use :func:`seek_localize.bids.read_dig_bids`.

    Parameters
    ----------
    sensors : Sensors
        An instance of the electrode sensors with the coordinates,
        coordinate system and coordinate units.
    to_coord : str
        The type of coordinate unit to convert to. Must be one of
        ``['mri', 'tkras', 'mni']``. ``tkras`` is the FreeSurfer
        special RAS space, described in Notes. ``mni`` is the Montreal
        Neurological Institute space, corresponding to the ``fsaverage``
        subject in FreeSurfer.
    subjects_dir : str | pathlib.Path
        The FreeSurfer ``SUBJECTS-DIR`` that houses the output of FreeSurfer
        reconstruction. A matching subject corresponding to the subject
        of ``sensors`` should be in there. Only required if
        ``to_coord = 'mni'``.
    verbose : bool
        Verbosity.

    Returns
    -------
    sensors : Sensors
        The electrode sensors with converted coordinates.

    Notes
    -----
    ``Nibabel`` processes everything in units of ``millimeters``.

    To convert from xyz (e.g. 'mm') to voxel and vice versa, one
    simply needs the ``IntendedFor`` image that contains the affine
    ``vox2ras`` transformation. For example, this might be a T1w
    image. One can use :func:`nibabel.affines.apply_affine` to then
    apply the corresponding transformation from vox to xyz space.

    Note, if you want to go from xyz to vox, then you need the
    inverse of the ``vox2ras`` transformation.

    If one wants to convert to ``tkras``, which is FreeSurfer's
    surface xyz space, this is the xyz space of the closest surface [1,2,3].
    This corresponds to the `vox2rask_tkr <https://nipy.org/nibabel/reference/nibabel.freesurfer.html#nibabel.freesurfer.mghformat.MGHHeader.get_vox2ras_tkr>`_  # noqa
    function in ``nibabel``. The ``tkrvox2ras`` transformation can
    be obtained from FreeSurfer's ``mri_info`` command via::

        mri_info --vox2ras-tkr <img>

    This will generally be the 4x4 matrix for FreeSurfer output.::

            [
                [-1.0, 0.0, 0.0, 128.0],
                [0.0, 0.0, 1.0, -128.0],
                [0.0, -1.0, 0.0, 128.0],
                [0.0, 0.0, 0.0, 1.0],
            ]

    but may be different depending on how some FreeSurfer hyperparameters.

    References
    ----------
    .. [1] FieldTrip explanation: https://www.fieldtriptoolbox.org/faq/how_are_the_different_head_and_mri_coordinate_systems_defined/#details-of-the-freesurfer-coordinate-system  # noqa

    .. [2] How MNE handles FreeSurfer data: https://mne.tools/dev/auto_tutorials/source-modeling/plot_background_freesurfer_mne.html  # noqa

    .. [3] FreeSurfer Wiki: https://surfer.nmr.mgh.harvard.edu/fswiki/CoordinateSystems  # noqa
    """
    if to_coord not in MAPPING_COORD_FRAMES:
        raise ValueError(
            f"Converting coordinates to {to_coord} "
            f"is not accepted. Please use one of "
            f"{MAPPING_COORD_FRAMES} coordinate systems."
        )
    if sensors.coord_unit not in MAPPING_COORD_FRAMES:
        raise ValueError(
            f"Converting coordinates requires sensor "
            f"coordinate space to be in 'voxel' space for "
            f"a particular coordinate system. Please use one of "
            f"{MAPPING_COORD_FRAMES} coordinate systems. "
        )

    if to_coord == sensors.coord_unit:
        return sensors

    # get the image file path
    img_fpath = sensors.intended_for
    if img_fpath is None:
        raise RuntimeError(
            f"Need IntendedFor Image filepath for " f"sensors {sensors}."
        )
    img = nb.load(img_fpath)

    # get the actual xyz coordinates
    elec_coords = sensors.get_coords()

    # first convert to standardized MRI coordinates
    if sensors.coord_unit == "mni":
        # reverse MNI transform to MRI
        elec_coords = _handle_mni_trans(
            elec_coords=elec_coords,
            img_fpath=img_fpath,
            subjects_dir=subjects_dir,
            revert_mni=True,
            verbose=verbose,
        )
    elif sensors.coord_unit == "tkras":
        # reverse Tkras transform to MRI
        elec_coords = _handle_tkras_trans(
            elec_coords=elec_coords, img=img, revert_tkras=True, verbose=verbose
        )

    # next convert from standardized MRI coordinates -> desired coordinate system
    if to_coord == "mni":
        # reverse MNI transform to MRI
        elec_coords = _handle_mni_trans(
            elec_coords=elec_coords,
            img_fpath=img_fpath,
            subjects_dir=subjects_dir,  # type: ignore
            revert_mni=False,
            verbose=verbose,
        )
    elif to_coord == "tkras":
        if sensors.coord_unit not in ["mri", "mni"]:
            # first scale to millimeters if not already there
            elec_coords = _scale_coordinates(elec_coords, sensors.coord_unit, "mm")

        # MRI transform to tkras
        elec_coords = _handle_tkras_trans(
            elec_coords=elec_coords, img=img, revert_tkras=False, verbose=verbose
        )

    # recreate sensors
    sensors = Sensors(**sensors.__dict__)
    sensors.set_coords(elec_coords)
    sensors.coord_unit = to_coord
    return sensors


def convert_coord_units(
    sensors: Sensors, to_coord: str, round=True, verbose: bool = True
):
    """Convert electrode coordinates between voxel and xyz.

    To obtain the sensors, one can use :func:`seek_localize.bids.read_dig_bids`.

    Parameters
    ----------
    sensors : Sensors
        An instance of the electrode sensors with the coordinates,
        coordinate system and coordinate units.
    to_coord : str
        The type of coordinate unit to convert to. Must be one of
        ``['mri', 'mm']``. ``mri`` corresponds to voxel space of the
        FreeSurfer ``T1.mgz`` file.
    round : bool
        Whether to round the coordinates to the nearest integer.
    verbose : bool
        Verbosity.

    Returns
    -------
    sensors : Sensors
        The electrode sensors with converted coordinates.

    Notes
    -----
    This function SOLELY transforms between ``voxel`` and ``xyz`` (i.e. RAS)
    spaces. For converting between different standardized coordinate systems like
    ``tkras`` and ``mni``, then check out `~seek_localize.label.convert_coord_space`.

    References
    ----------
    .. [1] FieldTrip explanation: https://www.fieldtriptoolbox.org/faq/how_are_the_different_head_and_mri_coordinate_systems_defined/#details-of-the-freesurfer-coordinate-system  # noqa

    .. [2] How MNE handles FreeSurfer data: https://mne.tools/dev/auto_tutorials/source-modeling/plot_background_freesurfer_mne.html  # noqa

    .. [3] FreeSurfer Wiki: https://surfer.nmr.mgh.harvard.edu/fswiki/CoordinateSystems  # noqa
    """
    if to_coord not in ["mri", "mm"]:
        raise ValueError(
            f"Converting coordinates to {to_coord} "
            f"is not accepted. Please use one of "
            f"['mm', 'mri'] coordinate types."
        )

    if to_coord == sensors.coord_unit:
        return sensors

    # get the image file path
    img_fpath = sensors.intended_for
    img = nb.load(img_fpath)

    # voxel -> xyz
    affine = img.affine

    # xyz -> voxel
    inv_affine = np.linalg.inv(affine)

    # get the actual xyz coordinates
    elec_coords = sensors.get_coords()

    if verbose:
        print(
            f"Converting coordinates from {sensors.coord_unit} to "
            f"{to_coord} using {img_fpath}."
        )

    # apply the affine
    if to_coord == "mri":
        # first scale to millimeters if not already there
        elec_coords = _scale_coordinates(elec_coords, sensors.coord_unit, "mm")

        # now convert xyz to voxels
        elec_coords = apply_affine(inv_affine, elec_coords)
    elif to_coord == "mm":
        # xyz -> voxels
        elec_coords = apply_affine(affine, elec_coords)

    if round:
        # round it off to integer
        elec_coords = np.round(elec_coords).astype(int)

    # recreate sensors
    sensors = Sensors(**sensors.__dict__)
    sensors.set_coords(elec_coords)
    sensors.coord_unit = to_coord
    return sensors


def _handle_tkras_trans(
    elec_coords: np.ndarray,
    img: nb.Nifti2Image,
    revert_tkras: bool,
    verbose: bool = True,
):
    """Handle FreeSurfer MRI <-> TKRAS."""
    # get the voxel to tkRAS transform
    try:
        vox2ras_tkr = img.header.get_vox2ras_tkr()
    except AttributeError as e:
        warn(
            f"Unable to programmatically get vox2ras TKR, "
            f"so setting manually. "
            f"Error: {e}"
        )
        vox2ras_tkr = [
            [-1.0, 0.0, 0.0, 128.0],
            [0.0, 0.0, 1.0, -128.0],
            [0.0, -1.0, 0.0, 128.0],
            [0.0, 0.0, 0.0, 1.0],
        ]

    if verbose:
        print(f"Using Vox2TKRAS affine: {vox2ras_tkr}.")

    if revert_tkras:
        affine = np.linalg.inv(vox2ras_tkr)
    else:
        affine = vox2ras_tkr

    # now convert voxels to tkras
    elec_coords = apply_affine(affine, elec_coords)
    return elec_coords


def _handle_mni_trans(
    elec_coords,
    img_fpath: Union[str, Path],
    subjects_dir: Union[str, Path, None],
    revert_mni: bool,
    verbose: bool = True,
):
    """Handle FreeSurfer MRI <-> MNI voxels."""
    entities = get_entities_from_fname(img_fpath)
    subject = entities.get("subject")
    if subject is None:
        raise RuntimeError(
            f"Could not interpret the subject from "
            f"IntendedFor Image filepath {img_fpath}. "
            f"This file path is possibly not named "
            f"according to BIDS."
        )

    # Try to get Norig and Torig
    # (i.e. vox_ras_t and vox_mri_t, respectively)
    path = op.join(subjects_dir, subject, "mri", "orig.mgz")  # type: ignore
    if not op.isfile(path):
        path = op.join(subjects_dir, subject, "mri", "T1.mgz")  # type: ignore
    if not op.isfile(path):
        raise IOError("mri not found: %s" % path)
    _, _, mri_ras_t, _, _ = _read_mri_info(path)

    # get the intended affine transform from vox -> RAS
    img = nb.load(img_fpath)
    # voxel -> xyz
    intended_affine = img.affine

    # check that vox2ras is the same as our affine
    if not np.isclose(intended_affine, mri_ras_t):
        raise RuntimeError(
            f"You are trying to convert data "
            f"to MNI coordinates for {img_fpath}, "
            f"but this does not correspond to the "
            f"original T1.mgz file of FreeSurfer. "
            f"This is a limitation..."
        )

    # read mri voxel of T1.mgz -> MNI tal xfm
    mri_mni_t = read_talxfm(subject=subject, subjects_dir=subjects_dir, verbose=verbose)

    # if reversing MNI, invert affine transform
    # else keep the same as read in
    if revert_mni:
        affine = np.linalg.inv(mri_mni_t)
    else:
        affine = mri_mni_t

    # first convert to voxels
    elec_coords = apply_affine(affine, elec_coords)

    return elec_coords
