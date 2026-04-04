import { Router, type IRouter } from "express";
import healthRouter from "./health";
import zetabridgeRouter from "./zetabridge";

const router: IRouter = Router();

router.use(healthRouter);
router.use(zetabridgeRouter);

export default router;
