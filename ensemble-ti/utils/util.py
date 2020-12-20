import numpy as np
import scanpy as sc
import pandas as pd
import phenograph

from sklearn.decomposition import PCA


def preprocess_recipe(adata, min_expr_level=50, min_cells=10, use_hvg=True, n_top_genes=1500):
    preprocessed_data = adata.copy()
    print('Preprocessing....')
    sc.pp.filter_cells(preprocessed_data, min_counts=min_expr_level)
    print(f'\t->Removed cells with expression level<{min_expr_level}')

    sc.pp.filter_genes(preprocessed_data, min_cells=min_cells)
    print(f'\t->Removed genes expressed in <{min_cells} cells')

    sc.pp.normalize_total(preprocessed_data)
    log_transform(preprocessed_data)
    print('\t->Normalized data')

    if use_hvg:
        sc.pp.highly_variable_genes(preprocessed_data, n_top_genes=n_top_genes, flavor='cell_ranger')
        print(f'\t->Selected the top {n_top_genes} genes')
    print(f'Pre-processing complete. Updated data shape: {preprocessed_data.shape}')
    return preprocessed_data


def log_transform(data, pseudo_count=0.1):
    # Code taken from Palantir
    if type(data) is sc.AnnData:
        data.X = np.log2(data.X + pseudo_count) - np.log2(pseudo_count)
    else:
        return np.log2(data + pseudo_count)


def run_pca(data, n_components=300, use_hvg=True, variance=None, obsm_key=None, random_state=0):
    if not isinstance(data, sc.AnnData):
        raise Exception(f'Expected data to be of type sc.AnnData found: {type(data)}')

    data_df = data.to_df()
    if obsm_key is not None:
        data_df = data.obsm[obsm_key]
        if isinstance(data_df, np.ndarray):
            data_df = pd.DataFrame(data_df, index=data.obs_names, columns=data.var_names)

    # Select highly variable genes if enabled
    X = data_df.to_numpy()
    if use_hvg:
        valid_cols = data_df.columns[data.var['highly_variable'] == True]
        X = data_df[valid_cols].to_numpy()

    if variance is not None:
        # Determine the number of components dynamically
        pca = PCA(n_components=1000, random_state=random_state)
        pca.fit(X)
        try:
            n_comps = np.where(np.cumsum(pca.explained_variance_ratio_) > variance)[0][0]
        except IndexError:
            n_comps = n_components
    else:
        n_comps = n_components

    # Re-run with selected number of components (Either n_comps=n_components or
    # n_comps = minimum number of components required to explain variance)
    pca = PCA(n_components=n_comps, random_state=random_state)
    X_pca = pca.fit_transform(X)
    return X_pca, pca.explained_variance_ratio_, n_comps


def determine_cell_clusters(data, k=50, obsm_key='X_pca'):
    """Run phenograph for clustering cells"""
    if not isinstance(data, sc.AnnData):
        raise Exception(f'Expected data to be of type sc.AnnData found : {type(data)}')

    try:
        X = data.obsm[obsm_key]
    except KeyError:
        raise Exception(f'Either `X_pca` or `{obsm_key}` must be set in the data')
    communities, _, _ = phenograph.cluster(X, k=k)
    data.obsm['phenograph_communities'] = communities