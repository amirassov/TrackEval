import numpy as np
from scipy.optimize import linear_sum_assignment
from ._base_metric import _BaseMetric
from .. import _timing
from .. import utils


class Identity(_BaseMetric):
    """Class which implements the ID metrics"""

    @staticmethod
    def get_default_config():
        """Default class config values"""
        default_config = {
            'THRESHOLD': 0.5,  # Similarity score threshold required for a IDTP match. Default 0.5.
            'PRINT_CONFIG': True,  # Whether to print the config information on init. Default: False.
            'TRACK_IOU_THRESH': 0.2,  # intersection of gt and pred tracks should be larger then this thresh. thresh is relative to pred track length
        }
        return default_config

    def __init__(self, config=None):
        super().__init__()
        self.integer_fields = ['IDTP', 'IDFN', 'IDFP']
        self.float_fields = ['IDF1', 'IDR', 'IDP']
        self.fields = self.float_fields + self.integer_fields
        self.summary_fields = self.fields

        # Configuration options:
        self.config = utils.init_config(config, self.get_default_config(), self.get_name())
        self.threshold = float(self.config['THRESHOLD'])
        self.track_iou_threshold = float(self.config['TRACK_IOU_THRESH'])

    @_timing.time
    def eval_sequence(self, data):
        """Calculates ID metrics for one sequence"""
        # Initialise results
        res = {}
        for field in self.fields:
            res[field] = 0

        # Return result quickly if tracker or gt sequence is empty
        if data['num_tracker_dets'] == 0:
            res['IDFN'] = data['num_gt_dets']
            return self._compute_final_fields(res)
        if data['num_gt_dets'] == 0:
            res['IDFP'] = data['num_tracker_dets']
            return self._compute_final_fields(res)

        # Variables counting global association
        potential_matches_count = np.zeros((data['num_gt_ids'], data['num_tracker_ids']))
        gt_id_count = np.zeros(data['num_gt_ids'])
        tracker_id_count = np.zeros(data['num_tracker_ids'])

        # First loop through each timestep and accumulate global track information.
        for t, (gt_ids_t, tracker_ids_t) in enumerate(zip(data['gt_ids'], data['tracker_ids'])):
            # Count the potential matches between ids in each timestep
            matches_mask = np.greater_equal(data['similarity_scores'][t], self.threshold)
            match_idx_gt, match_idx_tracker = np.nonzero(matches_mask)
            potential_matches_count[gt_ids_t[match_idx_gt.tolist()].tolist(), tracker_ids_t[match_idx_tracker.tolist()].tolist()] += 1

            # Calculate the total number of dets for each gt_id and tracker_id.
            gt_id_count[gt_ids_t.tolist()] += 1
            tracker_id_count[tracker_ids_t.tolist()] += 1

        # Calculate optimal assignment cost matrix for ID metrics
        num_gt_ids = data['num_gt_ids']
        num_tracker_ids = data['num_tracker_ids']
        fp_mat = np.zeros((num_gt_ids + num_tracker_ids, num_gt_ids + num_tracker_ids))
        fn_mat = np.zeros((num_gt_ids + num_tracker_ids, num_gt_ids + num_tracker_ids))
        fp_mat[num_gt_ids:, :num_tracker_ids] = 1e10
        fn_mat[:num_gt_ids, num_tracker_ids:] = 1e10
        for gt_id in range(num_gt_ids):
            fn_mat[gt_id, :num_tracker_ids] = gt_id_count[gt_id]
            fn_mat[gt_id, num_tracker_ids + gt_id] = gt_id_count[gt_id]
        for tracker_id in range(num_tracker_ids):
            fp_mat[:num_gt_ids, tracker_id] = tracker_id_count[tracker_id]
            fp_mat[tracker_id + num_gt_ids, tracker_id] = tracker_id_count[tracker_id]
        fn_mat[:num_gt_ids, :num_tracker_ids] -= potential_matches_count
        fp_mat[:num_gt_ids, :num_tracker_ids] -= potential_matches_count

        # Hungarian algorithm
        match_rows, match_cols = linear_sum_assignment(fn_mat + fp_mat)

        # Accumulate basic statistics
        res['IDFN'] = fn_mat[match_rows, match_cols].sum().astype(np.int)
        res['IDFP'] = fp_mat[match_rows, match_cols].sum().astype(np.int)
        res['IDTP'] = (gt_id_count.sum() - res['IDFN']).astype(np.int)

        # Calculate final ID scores
        res = self._compute_final_fields(res)
        return res

    def combine_classes_class_averaged(self, all_res, ignore_empty_classes=False):
        """Combines metrics across all classes by averaging over the class values.
        If 'ignore_empty_classes' is True, then it only sums over classes with at least one gt or predicted detection.
        """
        res = {}
        for field in self.integer_fields:
            if ignore_empty_classes:
                res[field] = self._combine_sum({k: v for k, v in all_res.items()
                                                if v['IDTP'] + v['IDFN'] + v['IDFP'] > 0 + np.finfo('float').eps},
                                               field)
            else:
                res[field] = self._combine_sum({k: v for k, v in all_res.items()}, field)
        for field in self.float_fields:
            if ignore_empty_classes:
                res[field] = np.mean([v[field] for v in all_res.values()
                                      if v['IDTP'] + v['IDFN'] + v['IDFP'] > 0 + np.finfo('float').eps], axis=0)
            else:
                res[field] = np.mean([v[field] for v in all_res.values()], axis=0)
        return res

    def combine_classes_det_averaged(self, all_res):
        """Combines metrics across all classes by averaging over the detection values"""
        res = {}
        for field in self.integer_fields:
            res[field] = self._combine_sum(all_res, field)
        res = self._compute_final_fields(res)
        return res

    def combine_sequences(self, all_res):
        """Combines metrics across all sequences"""
        res = {}
        for field in self.integer_fields:
            res[field] = self._combine_sum(all_res, field)
        res = self._compute_final_fields(res)
        return res

    @staticmethod
    def _compute_final_fields(res):
        """Calculate sub-metric ('field') values which only depend on other sub-metric values.
        This function is used both for both per-sequence calculation, and in combining values across sequences.
        """
        if  res['IDFN'] != 0:
            res['IDR'] = res['IDTP'] / np.maximum(1.0, res['IDTP'] + res['IDFN'])
        else:
            res['IDR'] = 1
        if res['IDFP'] != 0:
            res['IDP'] = res['IDTP'] / np.maximum(1.0, res['IDTP'] + res['IDFP'])
        else:
            res['IDP'] = 1
        if res['IDFN'] != 0 or res['IDFP'] != 0:
            res['IDF1'] = res['IDTP'] / np.maximum(1.0, res['IDTP'] + 0.5 * res['IDFP'] + 0.5 * res['IDFN'])
        else:
            res['IDF1'] = 1
        return res


class TrackIdentity(Identity):
    @_timing.time
    def eval_sequence(self, data):
        """Calculates ID metrics for one sequence"""
        # Initialise results
        res = {}
        for field in self.fields:
            res[field] = 0

        # Return result quickly if tracker or gt sequence is empty
        if data['num_tracker_dets'] == 0:
            res['IDFN'] = data['num_gt_ids']
            return res
        if data['num_gt_dets'] == 0:
            res['IDFP'] = data['num_tracker_ids']
            return res

        # Variables counting global association
        potential_matches_count = np.zeros((data['num_gt_ids'], data['num_tracker_ids']))
        gt_id_count = np.zeros(data['num_gt_ids'])
        tracker_id_count = np.zeros(data['num_tracker_ids'])

        # First loop through each timestep and accumulate global track information.
        for t, (gt_ids_t, tracker_ids_t) in enumerate(zip(data['gt_ids'], data['tracker_ids'])):
            # Count the potential matches between ids in each timestep
            matches_mask = np.greater_equal(data['similarity_scores'][t], self.threshold)
            match_idx_gt, match_idx_tracker = np.nonzero(matches_mask)
            potential_matches_count[gt_ids_t[match_idx_gt.tolist()].tolist(), tracker_ids_t[match_idx_tracker.tolist()].tolist()] += 1

            # Calculate the total number of dets for each gt_id and tracker_id.
            gt_id_count[gt_ids_t.tolist()] += 1
            tracker_id_count[tracker_ids_t.tolist()] += 1

        # Calculate optimal assignment cost matrix for ID metrics
        num_gt_ids = data['num_gt_ids']
        num_tracker_ids = data['num_tracker_ids']
        fp_mat = np.zeros((num_gt_ids + num_tracker_ids, num_gt_ids + num_tracker_ids))
        fn_mat = np.zeros((num_gt_ids + num_tracker_ids, num_gt_ids + num_tracker_ids))
        fp_mat[num_gt_ids:, :num_tracker_ids] = 1
        fn_mat[:num_gt_ids, num_tracker_ids:] = 1
        for gt_id in range(num_gt_ids):
            fn_mat[gt_id, :num_tracker_ids] = gt_id_count[gt_id]
            fn_mat[gt_id, num_tracker_ids + gt_id] = 1.0 - 1e-6
        for tracker_id in range(num_tracker_ids):
            fp_mat[:num_gt_ids, tracker_id] = tracker_id_count[tracker_id]
            fp_mat[tracker_id + num_gt_ids, tracker_id] = 1.0 - 1e-6
        fn_mat[:num_gt_ids, :num_tracker_ids] -= potential_matches_count
        fp_mat[:num_gt_ids, :num_tracker_ids] -= potential_matches_count
        cost_matrix = fn_mat + fp_mat
        cost_matrix[:num_gt_ids, :num_tracker_ids] /= (cost_matrix[:num_gt_ids, :num_tracker_ids] + potential_matches_count)
        cost_matrix[num_gt_ids:, num_tracker_ids:] = 1

        # Hungarian algorithm for IDF1
        match_rows, match_cols = linear_sum_assignment(cost_matrix)
        match_mask = np.logical_and(match_rows < num_gt_ids, match_cols < num_tracker_ids)
        # pred tracks with relative iou to predicted track length lower then thresh are sent to FN
        re_assigned = np.sum(np.less_equal(potential_matches_count[match_rows[match_mask],
                                                                   match_cols[match_mask]] /
                                           tracker_id_count[match_cols[match_mask]],
                                           self.track_iou_threshold))  # relative to predicted track length intersection
        # Hungarian algorithm for track_IDF1
        res['IDFN'] = np.sum(np.logical_and(match_rows < num_gt_ids, match_cols >= num_tracker_ids)).astype(np.int) + re_assigned
        res['IDFP'] = np.sum(np.logical_and(match_rows >= num_gt_ids, match_cols < num_tracker_ids)).astype(np.int) + re_assigned  # sent to sink pred tracks counts as FP
        res['IDTP'] = (num_gt_ids - res['IDFN']).astype(np.int)

        # Calculate final ID scores
        res = self._compute_final_fields(res)
        return res

    def combine_sequences(self, all_res, average: str = "macro"):
        """Combines metrics across all sequences"""
        res = {}
        for field in self.integer_fields:
            res[field] = self._combine_sum(all_res, field)

        if average == "micro":
            res = self._compute_final_fields(res)
        elif average == "macro":
            for field in self.float_fields:
                res[field] = np.mean([all_res[k][field] for k in all_res.keys()]).astype(float)
        else:
            raise ValueError(f"Unexpected average value: {average}")
        return res
