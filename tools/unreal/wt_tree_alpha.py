"""Source-content contracts for ordinary-tree separate alpha in this Flanders package.

The public plain tree shader uses T0.a, while its alpha_split variant uses T1.x.
These 25 original material identities carry opaque T0 plus an independent gray
coverage T1 despite their plain shader names. Selection is limited to inspected
material IDs and PNG hashes; facing leaves, trunks and other trees keep T0.a.
"""
SOURCE_AUDIT_SHA256 = '67f35df9251d377a92641d61481b697b0167c21b9d19b0b05bb268aa6b41f18f'
SOURCE_MANIFEST_SHA256 = 'ac0afa8a40d90feee51bc53d02b41aa1b33c10b9f3d2a574b0e7d7be1f18c7d9'
TEXTURE_PAIRS = {
    "tree_populus_branches_flowers_tex_d": {
        "alpha": "tree_populus_branches_flowers_tex_a",
        "color_sha256": "ea82e2eb60bbdacef3a8d880166d5ff62e2545dbd72a38a633d04100bf3f2b76",
        "alpha_sha256": "3ad3dd6f24c3cfb604c7eab51a5799fcee5ea0529e63f800a3fb5e7b12d9a97b"
    },
    "ivy_branches_a_tex_d": {
        "alpha": "ivy_branches_a_tex_a",
        "color_sha256": "b359bcba024f5b7ff84306d518cd97cc791b4269dc4e1c35d1dc039478faa01a",
        "alpha_sha256": "826d6c9250cd5b476e1243e696c5835f69e6c6c6c6f9a52d8ea3ad3c627e161c"
    },
    "grape_branches_tex_d": {
        "alpha": "grape_branches_tex_a",
        "color_sha256": "fb07f85fa08347c6f0e863113a988aa7b40e2ccea1c305a200b275b1394274a4",
        "alpha_sha256": "1076703dad299d7195d8b67864bf432437a0a71c6b66da024e9ca78de3d9c2aa"
    },
    "tree_branch_dry_burnt_a_tex_d": {
        "alpha": "tree_branch_dry_burnt_a_tex_a",
        "color_sha256": "8fb7652aa0bb3b90d8635e51ea9f33eddf8974ff9fb2400ab9b4d7e8038c5cc3",
        "alpha_sha256": "0d31cee8bbc3336f69f4c9dafd090a26e886af7ac6f0fef88bea827feeeb785f"
    },
    "tree_populus_branches_tex_d": {
        "alpha": "tree_populus_branches_tex_a",
        "color_sha256": "f779e8d4077051b25aada1811fa931a34274676e606513e15ef9c8a4a05b1a1e",
        "alpha_sha256": "a30d2aa571b157ef45f62e1ec18ac9ffe9d21cb93c30bc5ba56af54b08c4bbf4"
    },
    "tree_poplar_branches_tex_d": {
        "alpha": "tree_poplar_branches_tex_a",
        "color_sha256": "30fe292c525f39aa869eeacaaeba2f20d86f1d70689f5f35e7d1af8aab27b046",
        "alpha_sha256": "02a8e1cca84f95dededb72ffc4130f440567e4f9fe5455ff71d73793c73d0340"
    },
    "tree_branches_tilia_b_tex_d": {
        "alpha": "tree_branches_tilia_b_tex_a",
        "color_sha256": "df271da6b7ad5e982b660c9765a2fcc80dda0b820f34e9e4db43c346a129d4b4",
        "alpha_sha256": "ed7398bb58c59fc1827231d14a5ad23568a8033787c3553aedeeaf67d1554750"
    }
}
MATERIALS = {
    "WT_51b981ff52bc7761fcba": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_populus_branches_flowers_tex_d"
    },
    "WT_14c453b71387047b1282": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_populus_branches_flowers_tex_d"
    },
    "WT_2ce004bb88a7bcdf95c2": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_populus_branches_flowers_tex_d"
    },
    "WT_a7e4c3af15a15b9a10b8": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "ivy_branches_a_tex_d"
    },
    "WT_17cfa4e50ed89c874bd0": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "ivy_branches_a_tex_d"
    },
    "WT_e6766b37d5fe7e3b5c98": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "grape_branches_tex_d"
    },
    "WT_392835424f911ce1646c": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_5fa91c887b06d42627e5": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_2e5c6bc89106fde99258": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_4861839a30bcacf1aaf7": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_6520f42fbc01e604ad0c": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_496f43a49bcdef0f450b": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_454544fafae0bb7d99a1": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_0a7da223dfc93f56afa5": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_976995997d3308e2a08b": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_f388b2b4d7c3cc9476b8": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branch_dry_burnt_a_tex_d"
    },
    "WT_5e87f6737452b5e124e9": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_populus_branches_tex_d"
    },
    "WT_5e5e4a882c2f813fd5b5": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_populus_branches_tex_d"
    },
    "WT_86f74a7afa62f85d902e": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_populus_branches_tex_d"
    },
    "WT_3bc7913a1a2590a83acd": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_populus_branches_tex_d"
    },
    "WT_4a9616a400138db67d15": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_populus_branches_tex_d"
    },
    "WT_592b4ce8fd7e259f495a": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_poplar_branches_tex_d"
    },
    "WT_9210d424dcbbcf9d80df": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_poplar_branches_tex_d"
    },
    "WT_024791ff3c42986b1880": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branches_tilia_b_tex_d"
    },
    "WT_21ae14458d6848e2948f": {
        "shader": "rendinst_tree_colored",
        "atest": 127,
        "color": "tree_branches_tilia_b_tex_d"
    }
}


def tree_alpha_contract(spec, inventory=None):
    record = MATERIALS.get(spec.get('id'))
    if record is None:
        return None
    if spec['shader'] != record['shader']:
        raise ValueError('tree split-alpha source shader differs from audited material')
    slots = spec['texture_slots']
    color = slots[0].split('*')[0].split('$')[0] if slots[0] else ''
    alpha = slots[1].split('*')[0].split('$')[0] if slots[1] else ''
    pair = TEXTURE_PAIRS[record['color']]
    if color != record['color'] or alpha != pair['alpha']:
        raise ValueError('tree split-alpha source texture pair differs from audited material')
    if inventory is not None:
        for name, expected in ((color, pair['color_sha256']), (alpha, pair['alpha_sha256'])):
            if inventory.get(name, {}).get('sha256') != expected:
                raise ValueError('tree split-alpha source PNG hash differs: ' + name)
    return dict(record, alpha=alpha, color_sha256=pair['color_sha256'],
                alpha_sha256=pair['alpha_sha256'], sampling_channel='T1.r',
                audit_sha256=SOURCE_AUDIT_SHA256)
