"""Clip detail-view CAD polylines to a rectangular drawing window."""


def clip_polyline(points, bounds):
    xmin,xmax,ymin,ymax=bounds
    for start,end in zip(points,points[1:]):
        x,y=start;dx=end[0]-x;dy=end[1]-y;t0,t1=0.,1.
        for p,q in ((-dx,x-xmin),(dx,xmax-x),(-dy,y-ymin),(dy,ymax-y)):
            if abs(p)<1e-14:
                if q<0:break
            elif p<0:t0=max(t0,q/p)
            else:t1=min(t1,q/p)
            if t0>t1:break
        else:
            yield [(x+t0*dx,y+t0*dy),(x+t1*dx,y+t1*dy)]
