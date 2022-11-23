import numpy as np
import pandas as pd

def get_temporal_score(
    ad,
    comm_key="metric_clusters",
    data_key="Time_points",
):
    # Todo: Go through all the clusters. The temporal score for that cluster is the average of time of all the cells 
    #       Rather than giving a psuedo time give a temporal score
    #       Now, in the creation of graph change the directed edge
    communities = ad.obs[comm_key]
    cluster_ids = np.unique(communities)
    time = pd.DataFrame(ad.obs[data_key], index=ad.obs_names)
    # Create cluster index
    clusters = {}
    num_of_gene = ad.shape[0]
    score = pd.Series(0.00, index=[x for x in range(0,cluster_ids)])
    sz = pd.Series(0.00, index=[x for x in range(0,cluster_ids)])
    for idx in cluster_ids:
        cluster_idx = communities == idx
        clusters[idx] = cluster_idx  
    # Prune the initial adjacency matrix
    for i in range(0,num_of_gene):
        score[communities[i]] += time[i]
        sz[communities[i]] += 1

    for idx in cluster_ids:
        score[idx] = score[idx]/communities

    ad.obs["temporal_score"] = score
    return score