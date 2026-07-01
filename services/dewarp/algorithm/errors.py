class DewarpImageError(Exception):
    pass


class NotFoundYOLOSegmentError(DewarpImageError):
    pass


class HullNotFourPointsError(DewarpImageError):
    pass

class HullNotFoundError(DewarpImageError):
    pass

