import numpy as np
import pandas as pd
import phenograph
import scanpy as sc
import time

from functools import wraps
from scipy.sparse.csgraph import dijkstra
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import pairwise_distances



def compute_runtime(func):
    @wraps(func)
    def f(*args, **kwargs):
        start_time = time.time()
        r = func(*args, **kwargs)
        end_time = time.time()
        print(f'Runtime for {func.__name__}(): {end_time - start_time}')
        return r
    return f


@compute_runtime
def preprocess_recipe(adata, min_expr_level=None, min_cells=None, use_hvg=True, scale=False, n_top_genes=1500, pseudo_count=1.0):
    preprocessed_data = adata.copy()
    print('Preprocessing....')

    if min_expr_level is not None:
        sc.pp.filter_cells(preprocessed_data, min_counts=min_expr_level)
        print(f'\t->Removed cells with expression level<{min_expr_level}')

    if min_cells is not None:
        sc.pp.filter_genes(preprocessed_data, min_cells=min_cells)
        print(f'\t->Removed genes expressed in <{min_cells} cells')

    sc.pp.normalize_total(preprocessed_data)
    log_transform(preprocessed_data, pseudo_count=pseudo_count)
    print('\t->Normalized data')

    if use_hvg:
        sc.pp.highly_variable_genes(preprocessed_data, n_top_genes=n_top_genes, flavor='cell_ranger')
        print(f'\t->Selected the top {n_top_genes} genes')

    if scale:
        print('\t->Applying z-score normalization')
        sc.pp.scale(preprocessed_data)
    print(f'Pre-processing complete. Updated data shape: {preprocessed_data.shape}')
    return preprocessed_data


def log_transform(data, pseudo_count=1):
    if type(data) is sc.AnnData:
        data.X = np.log2(data.X + pseudo_count) - np.log2(pseudo_count)
    else:
        return np.log2(data + pseudo_count)


@compute_runtime
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
        comps_ = min(X.shape[0], X.shape[1])
        pca = PCA(n_components=comps_, random_state=random_state)
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


@compute_runtime
def determine_cell_clusters(data, obsm_key='X_pca', backend='phenograph', cluster_key='clusters', nn_kwargs={}, **kwargs):
    """Run clustering of cells"""
    if not isinstance(data, sc.AnnData):
        raise Exception(f'Expected data to be of type sc.AnnData found : {type(data)}')
    try:
        X = data.obsm[obsm_key]
    except KeyError:
        raise Exception(f'Either `X_pca` or `{obsm_key}` must be set in the data')
    if backend == 'phenograph':
        clusters, _, score = phenograph.cluster(X, **kwargs)
        data.obs[cluster_key] = clusters
    elif backend == 'kmeans':
        kmeans = KMeans(**kwargs)
        clusters = kmeans.fit_predict(X)
        score = kmeans.inertia_
        data.obs[cluster_key] = clusters
    elif backend == 'louvain':
        # Compute nearest neighbors
        sc.pp.neighbors(data, use_rep=obsm_key, **nn_kwargs)
        sc.tl.louvain(data, key_added=cluster_key, **kwargs)
        data.obs[cluster_key] = data.obs[cluster_key].to_numpy().astype(np.int)
        clusters = data.obs[cluster_key]
        score = None
    elif backend == 'leiden':
        # Compute nearest neighbors
        sc.pp.neighbors(data, use_rep=obsm_key, **nn_kwargs)
        sc.tl.leiden(data, key_added=cluster_key, **kwargs)
        data.obs[cluster_key] = data.obs[cluster_key].to_numpy().astype(np.int)
        clusters = data.obs[cluster_key]
        score = None
    else:
        raise NotImplementedError(f'The backend {backend} is not supported yet!')
    return clusters, score


def get_start_cell_cluster_id(data, start_cell_ids, communities):
    start_cluster_ids = set()
    obs_ = data.obs_names
    for cell_id in start_cell_ids:
        start_cell_idx = np.where(obs_ == cell_id)[0][0]
        start_cell_cluster_idx = communities[start_cell_idx]
        start_cluster_ids.add(start_cell_cluster_idx)
    return start_cluster_ids


def prune_network_edges(communities, adj_sc, adj_cluster):
    cluster_ids = np.unique(communities)
    n_pruned = 0

    # Create cluster index
    clusters = {}
    for idx in cluster_ids:
        cluster_idx = communities == idx
        clusters[idx] = cluster_idx

    col_ids = adj_cluster.columns
    for c_idx in adj_cluster.index:
        cluster_i = clusters[c_idx]
        non_connected_clusters = col_ids[adj_cluster.loc[c_idx, :] == 0]
        for nc_idx in non_connected_clusters:
            if nc_idx == c_idx:
                continue
            cluster_nc = clusters[nc_idx]
            adj_i_nc = adj_sc.loc[cluster_i, cluster_nc]

            # Keep track of number of edges pruned for book-keeping!
            n_pruned += np.sum(adj_i_nc.to_numpy() > 0)

            # Prune (remove the edges between two non-connected clusters)
            adj_sc.loc[cluster_i, cluster_nc] = np.zeros_like(adj_i_nc).squeeze()

    print(f'Successfully pruned {n_pruned} edges')
    return adj_sc


def connect_graph(adj, data, start_cell_id):
    # TODO: Update the heuristic here which involves using the
    # cell with the max distance to establish a connection with
    # the disconnected parts of the clusters.

    index = adj.index
    dists = pd.Series(dijkstra(adj, indices=start_cell_id), index=index)
    unreachable_nodes = index[dists == np.inf]
    if len(unreachable_nodes) == 0:
        return adj

    # Connect unreachable nodes
    while len(unreachable_nodes) > 0:
        farthest_reachable_id = dists.loc[index[dists != np.inf]].idxmax()

        # Compute distances to unreachable nodes
        unreachable_dists = pairwise_distances(
            data.loc[farthest_reachable_id, :].values.reshape(1, -1),
            data.loc[unreachable_nodes, :],
        )
        unreachable_dists = pd.Series(
            np.ravel(unreachable_dists), index=unreachable_nodes
        )

        # Add edge between farthest reacheable and its nearest unreachable
        adj.loc[farthest_reachable_id, unreachable_dists.idxmin()] = unreachable_dists.min()

        # Recompute distances to early cell
        dists = pd.Series(dijkstra(adj, indices=start_cell_id), index=index)

        # Idenfity unreachable nodes
        unreachable_nodes = index[dists == np.inf]
    return adj
