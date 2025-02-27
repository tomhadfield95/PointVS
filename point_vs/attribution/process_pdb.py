from pathlib import Path

from pandas import DataFrame
from plip.basic import config
from plip.basic.supplemental import create_folder_if_not_exists, start_pymol
from plip.structure.preparation import PDBComplex
from plip.plipcmd import logger
from pymol import cmd

from point_vs.attribution.interaction_parser import StructuralInteractionParser
from point_vs.attribution.plip_subclasses import \
    PyMOLVisualizerWithBFactorColouring, VisualizerDataWithMolecularInfo
from point_vs.utils import mkdir


def visualize_in_pymol(
        model, attribution_fn, plcomplex, output_dir, model_args,
        only_process=None):
    """Visualizes the given Protein-Ligand complex at one site in PyMOL.

    This function is based on the origina plip.visualization.vizualise_in_pymol
    function, with the added functinoality which uses a machine learning model
    to colour the receptor atoms by
    """

    vis = PyMOLVisualizerWithBFactorColouring(plcomplex)

    #####################
    # Set everything up #
    #####################

    pdbid = plcomplex.pdbid
    lig_members = plcomplex.lig_members
    chain = plcomplex.chain
    if config.PEPTIDES:
        vis.ligname = 'PeptideChain%s' % plcomplex.chain
    if config.INTRA is not None:
        vis.ligname = 'Intra%s' % plcomplex.chain

    ligname = vis.ligname
    hetid = plcomplex.hetid

    metal_ids = plcomplex.metal_ids
    metal_ids_str = '+'.join([str(i) for i in metal_ids])

    ########################
    # Basic visualizations #
    ########################

    start_pymol(run=True, options='-pcq',
                quiet=not config.VERBOSE and not config.SILENT)
    vis.set_initial_representations()

    cmd.load(plcomplex.sourcefile)
    current_name = cmd.get_object_list(selection='(all)')[0]

    logger.debug(
        f'setting current_name to {current_name} and PDB-ID to {pdbid}')
    cmd.set_name(current_name, pdbid)
    cmd.hide('everything', 'all')
    if config.PEPTIDES:
        cmd.select(ligname, 'chain %s and not resn HOH' % plcomplex.chain)
    else:
        cmd.select(ligname, 'resn %s and chain %s and resi %s*' % (
            hetid, chain, plcomplex.position))
    logger.debug(
        f'selecting ligand for PDBID {pdbid} and ligand name {ligname}')
    logger.debug(
        f'resn {hetid} and chain {chain} and resi {plcomplex.position}')

    # Visualize and color metal ions if there are any
    if not len(metal_ids) == 0:
        vis.select_by_ids(ligname, metal_ids, selection_exists=True)
        cmd.show('spheres', 'id %s and %s' % (metal_ids_str, pdbid))

    # Additionally, select all members of composite ligands
    if len(lig_members) > 1:
        for member in lig_members:
            resid, chain, resnr = member[0], member[1], str(member[2])
            cmd.select(ligname, '%s or (resn %s and chain %s and resi %s)' % (
                ligname, resid, chain, resnr))

    cmd.show('sticks', ligname)
    cmd.color('myblue')
    cmd.color('myorange', ligname)
    cmd.util.cnc('all')
    if not len(metal_ids) == 0:
        cmd.color('hotpink', 'id %s' % metal_ids_str)
        cmd.hide('sticks', 'id %s' % metal_ids_str)
        cmd.set('sphere_scale', 0.3, ligname)
    cmd.deselect()

    vis.make_initial_selections()

    vis.show_hydrophobic()  # Hydrophobic Contacts
    vis.show_hbonds()  # Hydrogen Bonds
    vis.show_halogen()  # Halogen Bonds
    vis.show_stacking()  # pi-Stacking Interactions
    vis.show_cationpi()  # pi-Cation Interactions
    vis.show_sbridges()  # Salt Bridges
    vis.show_wbridges()  # Water Bridges
    vis.show_metal()  # Metal Coordination

    results_fname = Path(output_dir, '{0}_{1}_results.txt'.format(
        pdbid.upper(), '_'.join(
            [hetid, plcomplex.chain, plcomplex.position]))).expanduser()

    parser = StructuralInteractionParser()
    score, df = vis.colour_b_factors_pdb(
        model, attribution_fn=attribution_fn, parser=parser,
        results_fname=results_fname, model_args=model_args,
        only_process=only_process)

    if not isinstance(df, DataFrame):
        return None, None

    vis.refinements()
    vis.zoom_to_ligand()
    vis.selections_cleanup()
    vis.selections_group()
    vis.additional_cleanup()

    if config.DNARECEPTOR:
        # Rename Cartoon selection to Line selection and change repr.
        cmd.set_name('%sCartoon' % plcomplex.pdbid, '%sLines' % plcomplex.pdbid)
        cmd.hide('cartoon', '%sLines' % plcomplex.pdbid)
        cmd.show('lines', '%sLines' % plcomplex.pdbid)

    if config.PEPTIDES:
        filename = "%s_PeptideChain%s" % (pdbid.upper(), plcomplex.chain)
        if config.PYMOL:
            vis.save_session(config.OUTPATH, override=filename)
    elif config.INTRA is not None:
        filename = "%s_IntraChain%s" % (pdbid.upper(), plcomplex.chain)
        if config.PYMOL:
            vis.save_session(config.OUTPATH, override=filename)
    else:
        filename = '%s_%s' % (
            pdbid.upper(),
            "_".join([hetid, plcomplex.chain, plcomplex.position]))
        vis.save_session(plcomplex.mol.output_path, override=filename)
    if config.PICS:
        vis.save_picture(config.OUTPATH, filename)

    return score, df


def score_pdb(
        model, attribution_fn, pdbfile, outpath, model_args,
        only_process=None, quiet=False, save_ligand_sdf=False):
    mol = PDBComplex()
    outpath = str(Path(outpath).expanduser())
    pdbfile = str(Path(pdbfile).expanduser())
    mol.output_path = outpath

    mol.load_pdb(pdbfile, as_string=False)

    for ligand in mol.ligands:
        mol.characterize_complex(ligand)

    mkdir(outpath)

    complexes = [VisualizerDataWithMolecularInfo(mol, site) for site in sorted(
        mol.interaction_sets)
                 if not len(mol.interaction_sets[site].interacting_res) == 0]

    if save_ligand_sdf:
        ligand_outpath = str(Path(outpath, 'crystal_ligand.sdf'))
        complexes[0].ligand.molecule.write(
            format='sdf', filename=ligand_outpath, overwrite=True)
        print('Saved ligand as sdf to', ligand_outpath)

    def score_atoms(
            model, attribution_fn, plcomplex, model_args, only_process=None):

        vis = PyMOLVisualizerWithBFactorColouring(plcomplex)

        if config.PEPTIDES:
            vis.ligname = 'PeptideChain%s' % plcomplex.chain
        if config.INTRA is not None:
            vis.ligname = 'Intra%s' % plcomplex.chain

        parser = StructuralInteractionParser()
        score, df = vis.score_atoms(
            parser, only_process, model, attribution_fn, model_args=model_args,
            quiet=quiet)
        return df

    dfs = [score_atoms(
        model, attribution_fn=attribution_fn, plcomplex=plcomplex,
        model_args=model_args, only_process=only_process)
        for plcomplex in complexes]
    dfs = [df for df in dfs if df is not None]
    return dfs


def score_and_colour_pdb(
        model, attribution_fn, pdbfile, outpath, model_args, only_process=None):
    mol = PDBComplex()
    outpath = str(Path(outpath).expanduser())
    pdbfile = str(Path(pdbfile).expanduser())
    mol.output_path = outpath
    mol.load_pdb(pdbfile, as_string=False)

    for ligand in mol.ligands:
        mol.characterize_complex(ligand)

    create_folder_if_not_exists(outpath)

    complexes = [VisualizerDataWithMolecularInfo(mol, site) for site in sorted(
        mol.interaction_sets)
                 if not len(mol.interaction_sets[site].interacting_res) == 0]

    dfs = {
        plcomplex.uid: visualize_in_pymol(
            model,
            attribution_fn=attribution_fn,
            output_dir=outpath,
            plcomplex=plcomplex,
            model_args=model_args,
            only_process=only_process)
        for plcomplex in complexes
    }
    dfs = {lig_id: (score, df) for lig_id, (score, df)
           in dfs.items() if df is not None}
    return dfs
